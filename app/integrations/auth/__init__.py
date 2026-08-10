from app.integrations.auth.dingtalk import DingTalkIdentityProvider
from app.integrations.auth.disabled import DisabledAuthenticationAdapter
from app.integrations.auth.mock import MockAuthenticationAdapter
from app.integrations.auth.passwords import ScryptPasswordHasher
from app.integrations.auth.widget import StaticWidgetSiteAuthenticationAdapter
from app.integrations.auth.widget_database import (
    CompositeWidgetSiteAuthenticationAdapter,
    PostgreSQLWidgetSiteAuthenticationAdapter,
)
from app.integrations.auth.widget_tokens import (
    HmacPublicWidgetCursorAdapter,
    HmacPublicWidgetTokenAdapter,
)

__all__ = [
    "CompositeWidgetSiteAuthenticationAdapter",
    "DisabledAuthenticationAdapter",
    "DingTalkIdentityProvider",
    "MockAuthenticationAdapter",
    "PostgreSQLWidgetSiteAuthenticationAdapter",
    "HmacPublicWidgetTokenAdapter",
    "HmacPublicWidgetCursorAdapter",
    "ScryptPasswordHasher",
    "StaticWidgetSiteAuthenticationAdapter",
]
