# Mini Olist test sample

This directory is generated from the full Olist CSVs by:

```bash
make mini-data
```

It contains 10 orders (8 delivered, 2 canceled), their customer records, item
and payment rows, the referenced products, and matching category translations.
The sample preserves the two demo failure shapes:

- two persistent customers repeat, so grouping by `orders.customer_id` still
  reports 0% while `customers.customer_unique_id` yields 40.0000%;
- a multi-row order makes the payment join inflate sales (R$ 677.92 vs the
  R$ 393.61 item-price reference).

These are sample values for fast UI/runtime testing; the authoritative demo
numbers refer to the full dataset. Do not edit the CSVs by hand. The generated
CSV and manifest files are ignored by git; the generator script is tracked.

Use a separate process and run directory:

```bash
OLIST_DATA_DIR=./data/olist_mini RUNS_DIR=./runs_mini PORT=8001 ./scripts/start.sh
```
