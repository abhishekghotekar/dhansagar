# Convert customer JSON data into searchable text
def customer_to_text(data: dict) -> str:
    text = (
        f"Customer name {data['customer_name']} "
        f"address {data['customer_address']} "
        f"phone {data['customer_number']} "
        f"took {data['jar']} jar "
        f"paid {data['paid']} "
        f"pending {data['pending']} "
        f"date {data.get('date', 'N/A')} "
        f"payment success {data['success']}"
    )
    return text
