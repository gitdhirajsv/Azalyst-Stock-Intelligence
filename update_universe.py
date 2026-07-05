import urllib.request
import os

def fetch_nasdaq_trader_tickers():
    tickers = set()
    try:
        # Fetch Nasdaq listed
        req = urllib.request.Request("ftp://ftp.nasdaqtrader.com/symboldirectory/nasdaqlisted.txt")
        with urllib.request.urlopen(req) as response:
            lines = response.read().decode('utf-8').splitlines()
            for line in lines[1:-1]: # Skip header and footer
                parts = line.split('|')
                if len(parts) >= 6:
                    symbol = parts[0]
                    test_issue = parts[3]
                    financial_status = parts[4]
                    etf = parts[6]
                    if test_issue == 'N' and etf == 'N': # Filter out test issues and ETFs
                        tickers.add(symbol)
                        
        # Fetch Other listed (NYSE, AMEX)
        req2 = urllib.request.Request("ftp://ftp.nasdaqtrader.com/symboldirectory/otherlisted.txt")
        with urllib.request.urlopen(req2) as response:
            lines = response.read().decode('utf-8').splitlines()
            for line in lines[1:-1]:
                parts = line.split('|')
                if len(parts) >= 7:
                    symbol = parts[0]
                    test_issue = parts[6]
                    etf = parts[4]
                    if test_issue == 'N' and etf == 'N':
                        tickers.add(symbol)
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        
    return tickers

def main():
    print("Updating US Stock Universe from Nasdaq FTP...")
    tickers = fetch_nasdaq_trader_tickers()
    if not tickers:
        print("Failed to fetch tickers. Keeping existing global_tickers.csv")
        return
        
    print(f"Fetched {len(tickers)} raw tickers.")
    
    # Clean and filter
    valid_tickers = set()
    for t in tickers:
        # Filter out preferred shares, warrants, rights which have symbols like PR, WS, RT, or contain '-' or '.'
        if '-' in t or '.' in t or '$' in t:
            continue
        valid_tickers.add(t)
        
    print(f"Filtered to {len(valid_tickers)} clean equities.")
    
    csv_path = "global_tickers.csv"
    with open(csv_path, "w") as f:
        f.write("ticker\n")
        for t in sorted(valid_tickers):
            f.write(f"{t}\n")
    print(f"Saved {len(valid_tickers)} tickers to {csv_path}")

if __name__ == "__main__":
    main()
