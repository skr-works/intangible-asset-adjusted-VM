def calculate_intangible_value(ticker_code):
    code_str = str(ticker_code).strip()
    
    # --- 修正箇所: ここから ---
    # スプシから数値取得された場合の小数点(.0)除去
    if code_str.endswith('.0'):
        code_str = code_str[:-2]
        
    # 日本株コード(数字開始)以外や空欄が渡された場合は、ヘッダーとみなしてスキップ
    if not code_str or not code_str[0].isdigit():
        return [None, None, None, None, None, None, None, None, "スキップ(項目名等)"]
    
    symbol = f"{code_str}.T"
    # --- 修正箇所: ここまで ---

    ticker = yf.Ticker(symbol)

    def _safe_float(x):
        try:
            if x is None or (isinstance(x, float) and pd.isna(x)):
                return None
            return float(x)
        except Exception:
            return None

    def _sort_cols_desc(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        cols = list(df.columns)
        # yfinance の columns は Timestamp であることが多いが、型が混ざる場合もあるため保険をかける
        try:
            cols_sorted = sorted(cols, reverse=True)
        except Exception:
            cols_sorted = cols
        return df.loc[:, cols_sorted]

    def _get_latest_from_df(df: pd.DataFrame, keys) -> float | None:
        for k in keys:
            if k in df.index:
                s = df.loc[k].dropna()
                if not s.empty:
                    return _safe_float(s.iloc[0])
        return None

    def _get_series_from_fin(fin_df: pd.DataFrame, keys, years_index) -> pd.Series:
        for k in keys:
            if k in fin_df.index:
                s = fin_df.loc[k].reindex(years_index).fillna(0)
                # Yahoo の損益系は費用が負で来ることがあるので、無形投資としては正の支出に正規化
                try:
                    s = s.astype("float64").abs()
                except Exception:
                    s = s.apply(lambda v: abs(_safe_float(v) or 0.0))
                return s
        return pd.Series(0.0, index=years_index, dtype="float64")

    try:
        # -------------------------
        # 0) 市場データ（頑丈に取得）
        # -------------------------
        price = None
        market_cap = None

        # fast_info 優先（info より安定することが多い）
        try:
            fast = getattr(ticker, "fast_info", None)
            if fast:
                # キー名は環境で揺れることがあるため複数候補
                price = fast.get("last_price", fast.get("lastPrice", None))
                market_cap = fast.get("market_cap", fast.get("marketCap", None))
        except Exception:
            pass

        price = _safe_float(price)
        market_cap = _safe_float(market_cap)

        # history で補完（currentPrice が死んでるケース対策）
        if price is None or price <= 0:
            try:
                hist = ticker.history(period="5d", auto_adjust=False)
                if hist is not None and not hist.empty:
                    price = _safe_float(hist["Close"].dropna().iloc[-1])
            except Exception:
                pass

        # info で補完（marketCap/株数）
        try:
            info = ticker.info or {}
        except Exception:
            info = {}

        if market_cap is None or market_cap <= 0:
            mc = info.get("marketCap", None)
            market_cap = _safe_float(mc)

        if (market_cap is None or market_cap <= 0) and (price is not None and price > 0):
            shares = _safe_float(info.get("sharesOutstanding", None))
            if shares and shares > 0:
                market_cap = price * shares

        # price/market_cap が取れなくても計算自体は続行する（PBRは None になる）
        # -------------------------
        # 1) 財務データ取得
        # -------------------------
        bs = ticker.balance_sheet
        fin = ticker.financials

        if bs is None or bs.empty or fin is None or fin.empty:
            return [price, market_cap, None, None, None, None, None, None, "財務データなし"]

        bs = _sort_cols_desc(bs)
        fin = _sort_cols_desc(fin)

        # -------------------------
        # 2) 自己資本（統一）と計上無形の控除（取れれば）
        # -------------------------
        equity = _get_latest_from_df(
            bs,
            keys=[
                "Stockholders Equity",
                "Total Stockholder Equity",
                "Total Equity Gross Minority Interest",
                "Total Equity",
            ],
        )

        if equity is None:
            return [price, market_cap, None, None, None, None, None, None, "自己資本データなし"]

        # 計上無形（B/Sに存在すれば控除して有形化）
        # まず合算で取れる項目を優先し、なければ内訳を足す
        intangible_notes = []
        total_intangibles = None

        # 合算候補
        total_intangibles = _get_latest_from_df(
            bs,
            keys=[
                "Goodwill And Other Intangible Assets",
                "Intangible Assets",
                "Other Intangible Assets",
            ],
        )

        if total_intangibles is None:
            goodwill = _get_latest_from_df(bs, keys=["Goodwill"])
            other_int = _get_latest_from_df(bs, keys=["Other Intangible Assets"])
            # どちらか取れれば合算
            if (goodwill is not None) or (other_int is not None):
                total_intangibles = (goodwill or 0.0) + (other_int or 0.0)

        # 有形自己資本（定義統一）
        # total_intangibles が取れない場合は「控除なし」で統一し、ステータスで明示
        if total_intangibles is None:
            tangible_book_value = equity
            intangible_notes.append("無形控除なし")
        else:
            tangible_book_value = equity - total_intangibles

        tangible_book_value = _safe_float(tangible_book_value)

        # 自己資本が 0 以下は「割安」の土俵に乗りにくいので除外ステータスにする
        if tangible_book_value is None or pd.isna(tangible_book_value) or tangible_book_value <= 0:
            return [
                price,
                market_cap,
                tangible_book_value,
                None,
                None,
                None,
                None,
                None,
                "自己資本<=0",
            ]

        # -------------------------
        # 3) R&D と SG&A の取得（安全側。Operating Expense 代用はしない）
        # -------------------------
        years = fin.columns

        rnd_series = _get_series_from_fin(
            fin,
            keys=[
                "Research And Development",
                "Research Development",
                "Research & Development",
            ],
            years_index=years,
        )

        sga_series = _get_series_from_fin(
            fin,
            keys=[
                "Selling General And Administration",
                "Selling General and Administrative",
                "Selling, General And Administration",
                "Selling, General and Administrative",
            ],
            years_index=years,
        )

        # -------------------------
        # 4) 無形投資 I_int と無形ストック K_int（永続棚卸法）
        # I_int = 1.0*R&D + 0.3*SG&A, δ=0.2
        # -------------------------
        i_int_series = rnd_series + (0.3 * sga_series)

        # 実質データなし判定（全期間0）
        if (i_int_series.fillna(0) == 0).all():
            adj_book_value = tangible_book_value
            pbr = (market_cap / tangible_book_value) if (market_cap is not None and market_cap > 0) else None
            adj_pbr = pbr
            status = "無形資産投資データなし"
            if intangible_notes:
                status += " (" + ",".join(intangible_notes) + ")"
            return [
                price,
                market_cap,
                tangible_book_value,
                0.0,
                0.0,
                adj_book_value,
                pbr,
                adj_pbr,
                status,
            ]

        # 年次データが短すぎると K_int が過小になりがちなので注記（計算は継続）
        notes = []
        if len(i_int_series) < 4:
            notes.append("年次データ短い")

        delta = 0.2
        k_int = 0.0

        # columns は降順に揃えた想定：i=0 が最新年、i=1 が1年前…
        # 念のため enumerate は Series の順序に従うので、ここで再度 years の順序を固定
        i_int_series = i_int_series.reindex(years)

        for i, val in enumerate(i_int_series):
            v = _safe_float(val)
            if v is None:
                continue
            # 念押し：負が混ざった場合でもストックが負になるのを防ぐ
            v = abs(v)
            k_int += v * ((1.0 - delta) ** i)

        k_int = _safe_float(k_int) or 0.0

        # -------------------------
        # 5) 調整後指標
        # -------------------------
        adj_book_value = tangible_book_value + k_int

        pbr = (market_cap / tangible_book_value) if (market_cap is not None and market_cap > 0) else None
        adj_pbr = (market_cap / adj_book_value) if (market_cap is not None and market_cap > 0 and adj_book_value > 0) else None

        # 直近の I_int（最新年）
        try:
            latest_i_int = _safe_float(i_int_series.iloc[0]) or 0.0
        except Exception:
            latest_i_int = 0.0

        # ステータス
        status_parts = []
        if intangible_notes:
            status_parts.extend(intangible_notes)
        if notes:
            status_parts.extend(notes)

        status = "OK" if not status_parts else ("OK (" + ",".join(status_parts) + ")")

        return [
            price,                # D: 現在値
            market_cap,           # E: 時価総額
            tangible_book_value,  # F: 有形自己資本（定義統一）
            latest_i_int,         # G: 直近の無形資産投資(I_int)
            k_int,                # H: 推計無形資産ストック(K_int)
            adj_book_value,       # I: 調整後自己資本
            pbr,                  # J: 従来PBR(有形ベース)
            adj_pbr,              # K: 調整後PBR
            status                # L: ステータス
        ]

    except Exception as e:
        return [None, None, None, None, None, None, None, None, f"エラー: {str(e)}"]
