# Convert customer JSON data into searchable text
def customer_to_text(data: dict) -> str:
    text = (
        f"Customer name {data.get('customer_name', '')} "
        f"address {data.get('customer_address', '')} "
        f"phone {data.get('customer_number', '')} "
        f"took {data.get('jar', 0)} jar "
        f"paid {data.get('paid', 0.0)} "
        f"pending {data.get('pending', 0.0)} "
        f"date {data.get('date', 'N/A')} "
        f"payment success {data.get('success', False)}"
    )
    return text

