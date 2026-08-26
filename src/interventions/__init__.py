"""Phase 5 - intervention simulation and risk-based pricing.

Reads config/interventions.yaml for every [A] behavioural assumption and
config/params.yaml for every [D] economic one. Consumes
data/processed/m2_scores.parquet as the scored population; never re-fits it.
"""
