# Olist data

## Download

The CSV files are not stored in the Git history. Download `olist_csv.zip` from
the [data-v1 release](https://github.com/bingxiangch/DataStepper_PoC/releases/tag/data-v1)
and extract it so the files are in `data/olist/`.

Alternatively, download the *Brazilian E-Commerce Public Dataset by Olist*
(Kaggle) and place these six files in `data/olist/`:

- `olist_orders_dataset.csv`
- `olist_customers_dataset.csv`
- `olist_order_items_dataset.csv`
- `olist_order_payments_dataset.csv`
- `olist_products_dataset.csv`
- `product_category_name_translation.csv`

Then validate them:

```bash
python scripts/prepare_data.py
```

To stage the files from an extracted download:

```bash
python scripts/prepare_data.py --source /path/to/olist_csv
```

## Small test sample

For fast UI/runtime testing, generate a 10-order sample from the full CSVs:

```bash
make mini-data
# equivalent:
# python scripts/make_mini_dataset.py --force
# python scripts/prepare_data.py --dest data/olist_mini
```

The sample keeps the demo's two semantic failures:

- 8 delivered orders and 2 canceled orders across 10 customer records;
- two persistent customers repeat (reference **40.0000%**), while the wrong
  `orders.customer_id` plan still reports **0.0000%**;
- a delivered order has multiple item/payment rows, so the payment join inflates
  the category-sales total (**R$ 677.92** vs the item-price reference
  **R$ 393.61**).

These numbers are measured for the current sample and recorded in
`data/olist_mini/manifest.json`. They are for testing only; the authoritative
paper/demo values still refer to the full Olist dataset.

Run a separate server instance so the full-data instance and its runs are not
mixed:

```bash
OLIST_DATA_DIR=./data/olist_mini RUNS_DIR=./runs_mini PORT=8001 ./scripts/start.sh
```

Open http://127.0.0.1:8001. The sample is generated locally and both the CSVs
and manifest are ignored by git.

The debugger reads the CSVs directly and never modifies them. The only writable
data store is the disposable per-run SQLite sandbox under `runs/<run_id>/`.
The CSV files are not committed to the Git history; they are distributed as the
`olist_csv.zip` release asset under the Olist dataset's CC BY-NC-SA 4.0
license. Local CSV copies remain ignored by git.
