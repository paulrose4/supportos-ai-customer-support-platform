from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Customer:
    customer_id: str
    tenant_id: str
    display_name: str
