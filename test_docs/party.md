# party

| column_name       | data_type   | description                                      |
|-------------------|-------------|--------------------------------------------------|
| party_id          | varchar     | Unique identifier for the counterparty          |
| party_name        | varchar     | Legal name of the counterparty                   |
| party_type        | varchar     | Type (BANK, HEDGE_FUND, CORPORATE, INDIVIDUAL)   |
| lei_code          | varchar     | Legal Entity Identifier code                     |
| country           | varchar     | Country of incorporation                         |
| credit_rating     | varchar     | Current credit rating (AAA, AA, A, BBB, etc)     |
| credit_limit      | decimal     | Maximum exposure limit in USD                    |
| current_exposure  | decimal     | Current outstanding exposure                     |
| is_active         | boolean     | Whether the counterparty is active               |
| onboarding_date   | date        | Date when counterparty was onboarded             |
| primary_contact   | varchar     | Primary contact email                            |
| created_at        | timestamp   | Record creation timestamp                        |
