import pandas as pd

def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    # Drop empty rows/columns
    df = df.dropna(how="all")
    df = df.dropna(axis=1, how="all")

    # Normalize column names
    df.columns = [
        str(c).strip() if c and str(c).strip() else f"Column_{i}"
        for i, c in enumerate(df.columns)
    ]

    # Remove duplicate header rows
    header = list(df.columns)
    df = df[~df.apply(lambda row: list(row.values) == header, axis=1)]

    # Clean numeric columns
    for col in df.columns:
        if df[col].dtype == object:
            cleaned = (
                df[col]
                .astype(str)
                .str.replace("$", "", regex=False)
                .str.replace(",", "", regex=False)
                .str.replace(" ", "", regex=False)   # FIXES "$ 8 50,790"
                .str.replace("—", "", regex=False)
                .str.replace("-", "", regex=False)
            )

            numeric = pd.to_numeric(cleaned, errors="coerce")

            # Convert if majority numeric
            if numeric.notna().sum() > len(df) * 0.5:
                df[col] = numeric

    return df.reset_index(drop=True)
