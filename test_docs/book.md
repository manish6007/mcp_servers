# book

| column_name       | data_type   | description                                      |
|-------------------|-------------|--------------------------------------------------|
| book_id           | varchar     | Unique identifier for the trading book          |
| book_name         | varchar     | Display name of the trading book                 |
| book_type         | varchar     | Type (TRADING, BANKING, HEDGE)                   |
| desk              | varchar     | Trading desk responsible for this book           |
| region            | varchar     | Geographic region (AMER, EMEA, APAC)             |
| base_currency     | varchar     | Base currency for P&L reporting                  |
| risk_limit        | decimal     | Maximum VaR limit in base currency               |
| current_pnl       | decimal     | Current day P&L                                  |
| mtd_pnl           | decimal     | Month-to-date P&L                                |
| ytd_pnl           | decimal     | Year-to-date P&L                                 |
| manager_id        | varchar     | ID of the book manager                           |
| is_active         | boolean     | Whether the book is actively trading             |
| created_at        | timestamp   | Record creation timestamp                        |
