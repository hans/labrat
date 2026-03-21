import json
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

df = pd.read_csv(snakemake.input[0])
X = df.drop(columns=["species"])
y = df["species"]

X_train, X_test, y_train, y_test = train_test_split(
    X, y,
    test_size=snakemake.params.test_size,
    random_state=snakemake.params.random_state,
)

clf = RandomForestClassifier(n_estimators=100, random_state=snakemake.params.random_state)
clf.fit(X_train, y_train)
y_pred = clf.predict(X_test)

metrics = {
    "accuracy": accuracy_score(y_test, y_pred),
    "classification_report": classification_report(y_test, y_pred, output_dict=True),
    "n_train": len(X_train),
    "n_test": len(X_test),
}

with open(snakemake.output[0], "w") as f:
    json.dump(metrics, f, indent=2)

pred_df = pd.DataFrame({"true": y_test, "predicted": y_pred})
pred_df.to_csv(snakemake.output[1], index=False)
