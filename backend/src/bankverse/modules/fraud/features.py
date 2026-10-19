"""Leakage-safe transaction features for card fraud detection.

The *same* function is used for batch training (IBM TabFormer, millions of rows) and for online
scoring (a card's last 30 days fetched from PostgreSQL + the candidate transaction), which removes
train/serve skew. Every behavioural feature only looks at *strictly earlier* transactions of the
same card (``closed='left'`` windows, ``shift``), never at the label nor at future events.

Identifiers (user, card, merchant id) and calendar year are deliberately *not* features: they would
let a model memorise entities or periods instead of learning transferable behaviour.
"""

from __future__ import annotations

import polars as pl

ERROR_TYPES = {
    "err_insufficient_balance": "Insufficient Balance",
    "err_bad_pin": "Bad PIN",
    "err_bad_cvv": "Bad CVV",
    "err_bad_expiration": "Bad Expiration",
    "err_bad_card_number": "Bad Card Number",
    "err_technical_glitch": "Technical Glitch",
    "err_bad_zip": "Bad Zipcode",
}

BASE_COLUMNS = ("card_key", "ts", "amount", "use_chip", "merchant_id", "merchant_city", "merchant_state",
                "merchant_country", "mcc", "errors")

NUMERIC_FEATURES: list[str] = [
    "amount", "log_amount", "is_refund", "hour", "dow", "is_night", "is_online", "is_swipe", "is_chip",
    "is_foreign", "has_error", *ERROR_TYPES.keys(),
    "n_tx_1h", "n_tx_24h", "n_tx_7d", "n_tx_30d", "sum_amt_24h", "mean_amt_30d", "std_amt_30d",
    "amount_z_30d", "amount_ratio_30d", "max_amt_30d", "mins_since_last_tx", "merchant_new_30d", "mcc_new_30d",
    "region_new_30d", "online_share_30d", "foreign_share_30d", "err_24h", "refund_24h",
]
CATEGORICAL_FEATURES: list[str] = ["mcc"]
FEATURES: list[str] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

FEATURE_LABELS_FR: dict[str, str] = {
    "amount": "Montant", "log_amount": "Montant (log)", "is_refund": "Remboursement", "hour": "Heure",
    "dow": "Jour de semaine", "is_night": "Transaction nocturne (0h-5h)", "is_online": "Paiement en ligne",
    "is_swipe": "Piste magnétique", "is_chip": "Puce", "is_foreign": "Commerçant à l'étranger",
    "has_error": "Erreur d'autorisation", "err_insufficient_balance": "Solde insuffisant",
    "err_bad_pin": "Code PIN erroné", "err_bad_cvv": "CVV erroné", "err_bad_expiration": "Date d'expiration erronée",
    "err_bad_card_number": "Numéro de carte erroné", "err_technical_glitch": "Incident technique",
    "err_bad_zip": "Code postal erroné", "n_tx_1h": "Nb transactions 1h", "n_tx_24h": "Nb transactions 24h",
    "n_tx_7d": "Nb transactions 7j", "n_tx_30d": "Nb transactions 30j", "sum_amt_24h": "Montant cumulé 24h",
    "mean_amt_30d": "Montant moyen 30j", "std_amt_30d": "Écart-type montant 30j",
    "amount_z_30d": "Écart du montant à l'habitude (z-score)", "amount_ratio_30d": "Montant / moyenne 30j",
    "max_amt_30d": "Montant max 30j", "mins_since_last_tx": "Minutes depuis la transaction précédente",
    "merchant_new_30d": "Nouveau commerçant (30j)", "mcc_new_30d": "Nouvelle catégorie marchande (30j)",
    "region_new_30d": "Nouvelle région (30j)", "online_share_30d": "Part en ligne 30j",
    "foreign_share_30d": "Part à l'étranger 30j", "err_24h": "Erreurs 24h", "refund_24h": "Remboursements 24h",
    "mcc": "Catégorie marchande (MCC)",
}

MAX_GAP_MIN = 30 * 24 * 60


def compute_features(frame: pl.DataFrame | pl.LazyFrame, home_country: str) -> pl.LazyFrame:
    """Adds feature columns. Input must contain :data:`BASE_COLUMNS` (``ts`` as naive or UTC datetime)."""
    lf = frame.lazy() if isinstance(frame, pl.DataFrame) else frame
    lf = lf.sort(["card_key", "ts"])
    errs = pl.col("errors").fill_null("")
    lf = lf.with_columns(
        log_amount=pl.col("amount").abs().log1p(),
        is_refund=(pl.col("amount") < 0).cast(pl.Int8),
        hour=pl.col("ts").dt.hour().cast(pl.Int16),
        dow=pl.col("ts").dt.weekday().cast(pl.Int16),
        is_online=(pl.col("use_chip") == "Online Transaction").cast(pl.Int8),
        is_swipe=(pl.col("use_chip") == "Swipe Transaction").cast(pl.Int8),
        is_chip=(pl.col("use_chip") == "Chip Transaction").cast(pl.Int8),
        has_error=(errs.str.len_chars() > 0).cast(pl.Int8),
        **{k: errs.str.contains(v, literal=True).cast(pl.Int8) for k, v in ERROR_TYPES.items()},
        _one=pl.lit(1.0),
        _abs_amt=pl.col("amount").abs(),
    ).with_columns(
        is_night=(pl.col("hour") < 6).cast(pl.Int8),
        is_foreign=((pl.col("merchant_country") != home_country) & (pl.col("is_online") == 0)).cast(pl.Int8),
        _refund_f=pl.col("is_refund").cast(pl.Float64),
        _err_f=pl.col("has_error").cast(pl.Float64),
        _online_f=pl.col("is_online").cast(pl.Float64),
    ).with_columns(_foreign_f=pl.col("is_foreign").cast(pl.Float64))

    def roll_sum(col: str, period: str) -> pl.Expr:
        return pl.col(col).rolling_sum_by("ts", period, closed="left").over("card_key")

    lf = lf.with_columns(
        n_tx_1h=roll_sum("_one", "1h"),
        n_tx_24h=roll_sum("_one", "24h"),
        n_tx_7d=roll_sum("_one", "7d"),
        n_tx_30d=roll_sum("_one", "30d"),
        sum_amt_24h=roll_sum("_abs_amt", "24h"),
        err_24h=roll_sum("_err_f", "24h"),
        refund_24h=roll_sum("_refund_f", "24h"),
        mean_amt_30d=pl.col("_abs_amt").rolling_mean_by("ts", "30d", closed="left").over("card_key"),
        std_amt_30d=pl.col("_abs_amt").rolling_std_by("ts", "30d", closed="left").over("card_key"),
        max_amt_30d=pl.col("_abs_amt").rolling_max_by("ts", "30d", closed="left").over("card_key"),
        _online_30d=roll_sum("_online_f", "30d"),
        _foreign_30d=roll_sum("_foreign_f", "30d"),
        _prev_ts=pl.col("ts").shift(1).over("card_key"),
        _prev_merchant_ts=pl.col("ts").shift(1).over(["card_key", "merchant_id"]),
        _prev_mcc_ts=pl.col("ts").shift(1).over(["card_key", "mcc"]),
        _prev_region_ts=pl.col("ts").shift(1).over(["card_key", "merchant_state"]),
    )
    thirty = pl.duration(days=30)
    lf = lf.with_columns(
        n_tx_1h=pl.col("n_tx_1h").fill_null(0),
        n_tx_24h=pl.col("n_tx_24h").fill_null(0),
        n_tx_7d=pl.col("n_tx_7d").fill_null(0),
        n_tx_30d=pl.col("n_tx_30d").fill_null(0),
        sum_amt_24h=pl.col("sum_amt_24h").fill_null(0),
        err_24h=pl.col("err_24h").fill_null(0),
        refund_24h=pl.col("refund_24h").fill_null(0),
        mean_amt_30d=pl.col("mean_amt_30d").fill_null(0),
        std_amt_30d=pl.col("std_amt_30d").fill_null(0),
        max_amt_30d=pl.col("max_amt_30d").fill_null(0),
        mins_since_last_tx=(pl.col("ts") - pl.col("_prev_ts")).dt.total_minutes().cast(pl.Float64)
        .fill_null(MAX_GAP_MIN).clip(0, MAX_GAP_MIN),
        merchant_new_30d=(pl.col("_prev_merchant_ts").is_null() | ((pl.col("ts") - pl.col("_prev_merchant_ts")) > thirty)).cast(pl.Int8),
        mcc_new_30d=(pl.col("_prev_mcc_ts").is_null() | ((pl.col("ts") - pl.col("_prev_mcc_ts")) > thirty)).cast(pl.Int8),
        region_new_30d=(pl.col("_prev_region_ts").is_null() | ((pl.col("ts") - pl.col("_prev_region_ts")) > thirty)).cast(pl.Int8),
    ).with_columns(
        amount_z_30d=((pl.col("_abs_amt") - pl.col("mean_amt_30d")) / (pl.col("std_amt_30d") + 1.0)),
        amount_ratio_30d=(pl.col("_abs_amt") / (pl.col("mean_amt_30d") + 1.0)),
        online_share_30d=(pl.col("_online_30d").fill_null(0) / pl.col("n_tx_30d").clip(1, None)),
        foreign_share_30d=(pl.col("_foreign_30d").fill_null(0) / pl.col("n_tx_30d").clip(1, None)),
        mcc=pl.col("mcc").cast(pl.Int32),
    )
    return lf.drop([c for c in ("_one", "_abs_amt", "_refund_f", "_err_f", "_online_f", "_foreign_f", "_online_30d",
                                "_foreign_30d", "_prev_ts", "_prev_merchant_ts", "_prev_mcc_ts", "_prev_region_ts")])
