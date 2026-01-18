from tone import encode

data = {
    "table": "trades",
    "columns": ["trade_id", "amount", "counterparty"]
}

print(encode(data))
