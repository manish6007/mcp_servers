# trade

| column_name    | data_type   | description                                      |
|----------------|-------------|--------------------------------------------------|
| trade_id       | varchar     | Unique identifier for the trade                  |
| trade_date     | timestamp   | Date and time when the trade was executed        |
| settlement_date| date        | Expected settlement date of the trade            |
| trade_type     | varchar     | Type of trade (BUY, SELL, SHORT)                 |
| quantity       | decimal     | Number of units traded                           |
| price          | decimal     | Price per unit at execution                      |
| notional_value | decimal     | Total notional value (quantity * price)          |
| currency       | varchar     | Currency code (USD, EUR, GBP)                    |
| book_id        | varchar     | Reference to the trading book                    |
| party_id       | varchar     | Reference to the counterparty                    |
| trader_id      | varchar     | ID of the trader who executed                    |
| status         | varchar     | Trade status (PENDING, CONFIRMED, SETTLED)       |
| created_at     | timestamp   | Record creation timestamp                        |
