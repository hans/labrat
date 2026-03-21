import json
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay
from sklearn.decomposition import PCA

pred_df = pd.read_csv(snakemake.input.predictions)
data_df = pd.read_csv(snakemake.input.data)

with open(snakemake.input.metrics) as f:
    metrics = json.load(f)

# Confusion matrix
labels = sorted(pred_df["true"].unique())
cm = confusion_matrix(pred_df["true"], pred_df["predicted"], labels=labels)
fig, ax = plt.subplots(figsize=(6, 5))
ConfusionMatrixDisplay(cm, display_labels=labels).plot(ax=ax)
ax.set_title(f"Confusion Matrix (accuracy={metrics['accuracy']:.2%})")
fig.tight_layout()
fig.savefig(snakemake.output[0], dpi=100)
plt.close(fig)

# PCA scatter
X = data_df.drop(columns=["species"])
pca = PCA(n_components=2)
X_pca = pca.fit_transform(X)
fig, ax = plt.subplots(figsize=(7, 5))
for species in data_df["species"].unique():
    mask = data_df["species"] == species
    ax.scatter(X_pca[mask, 0], X_pca[mask, 1], label=species, alpha=0.7)
ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]:.1%})")
ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]:.1%})")
ax.set_title("Iris PCA")
ax.legend()
fig.tight_layout()
fig.savefig(snakemake.output[1], dpi=100)
plt.close(fig)
