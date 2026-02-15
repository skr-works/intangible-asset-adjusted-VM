def calculate_intangible_value(ticker_code):
    symbol = f"{str(ticker_code).strip()}.T"
    ticker = yf.Ticker(symbol)
    
    try:
        info = ticker.info
        price = info.get('currentPrice', info.get('previousClose', 0))
        market_cap = info.get('marketCap', 0)
        
        bs = ticker.balance_sheet
        fin = ticker.financials
        
        if bs is None or bs.empty or fin is None or fin.empty:
            return [price, market_cap, None, None, None, None, None, None, "財務データなし"]
            
        # 1. 有形自己資本 (Net Tangible Assets) を優先して取得
        # B/S上の既存の無形資産(のれん等)との二重計上を防ぐため
        tangible_book_value = None
        for key in ['Net Tangible Assets', 'Stockholders Equity', 'Total Stockholder Equity']:
            if key in bs.index and not bs.loc[key].isna().all():
                tangible_book_value = bs.loc[key].dropna().iloc[0]
                break
        
        if not tangible_book_value:
             return [price, market_cap, None, None, None, None, None, None, "自己資本データなし"]

        # 2. R&DとSG&Aの時系列データを取得 (yfinanceは左から新しい順 t, t-1, t-2...)
        # カラム(年)を揃えるためにDataFrameのまま処理する
        available_years = fin.columns
        
        # R&Dの取得 (なければ0)
        if 'Research And Development' in fin.index:
            rnd_series = fin.loc['Research And Development'].fillna(0)
        else:
            rnd_series = pd.Series(0, index=available_years)
            
        # SG&Aの取得 (なければOperating Expenseで代用、それもなければ0)
        if 'Selling General And Administration' in fin.index:
            sga_series = fin.loc['Selling General And Administration'].fillna(0)
        elif 'Operating Expense' in fin.index:
            sga_series = fin.loc['Operating Expense'].fillna(0)
        else:
            sga_series = pd.Series(0, index=available_years)

        # 3. 当期の無形資産投資 I_int の算出 (論文準拠)
        # I_int = 100% * R&D + 30% * SG&A
        i_int_series = rnd_series + (0.3 * sga_series)
        
        # データが全くない場合は計算不可
        if i_int_series.sum() == 0:
             return [price, market_cap, tangible_book_value, 0, 0, tangible_book_value, 
                     market_cap/tangible_book_value, market_cap/tangible_book_value, "無形資産投資ゼロ"]

        # 4. 永続卸売法による無形資産ストック K_int の推計 (償却率 delta = 20%)
        # yfinanceのデータは新しい順なので、i=0が最新年、i=1が1年前...
        # K_t = I_t + (1-δ)I_{t-1} + (1-δ)^2 I_{t-2} ...
        k_int = 0
        delta = 0.2
        for i, val in enumerate(i_int_series):
            if pd.notna(val):
                k_int += val * ((1 - delta) ** i)
            
        # 5. 調整後指標の計算
        adj_book_value = tangible_book_value + k_int
        
        # 従来のPBR (有形自己資本ベース) と 調整後PBR
        pbr = market_cap / tangible_book_value if tangible_book_value > 0 else None
        adj_pbr = market_cap / adj_book_value if adj_book_value > 0 else None
        
        return [
            price,                # D: 現在値
            market_cap,           # E: 時価総額
            tangible_book_value,  # F: 有形自己資本
            i_int_series.iloc[0], # G: 直近の無形資産投資(I_int)
            k_int,                # H: 推計無形資産ストック(K_int)
            adj_book_value,       # I: 調整後自己資本
            pbr,                  # J: 従来PBR(有形ベース)
            adj_pbr,              # K: 調整後PBR
            "OK"                  # L: ステータス
        ]
        
    except Exception as e:
        return [None, None, None, None, None, None, None, None, f"エラー: {str(e)}"]
