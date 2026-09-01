# /data

Nothing here is committed (see the root `.gitignore`) — everything is either
generated or downloaded. Expected contents once the pipeline is running:

| File | Produced by | Notes |
| --- | --- | --- |
| `synthetic_transactions.json` | Phase 1 — `data_generator.py` | Main demo dataset |
| `ground_truth.json` | Phase 1 — `data_generator.py` | Which wallets were deliberately made "guilty" |
| `graph.json` | Phase 2 — `graph_builder.py` | Serialized graph, for inspecting shape without Python |
| `elliptic_txs_features.csv` | Downloaded | Elliptic Bitcoin dataset |
| `elliptic_txs_classes.csv` | Downloaded | Labels: `1` = illicit, `2` = licit, `unknown` = unlabeled |
| `elliptic_txs_edgelist.csv` | Downloaded | Transaction graph edges |

The Elliptic dataset is on Kaggle as "Elliptic Data Set". Download it and drop
the three CSVs in here before running Phase 3.
