import pytest

from app.integrations.postgres.models import (
    ConversationModel,
    CustomerMemoryItemModel,
    CustomerModel,
    ExperienceEvalRunModel,
    ExperienceExperimentModel,
    ExperienceMemoryUsageModel,
    ExperienceReleaseModel,
    IssueOutcomeEpisodeModel,
    PlatformSiteDirectoryModel,
    PlatformTenantEntitlementModel,
    SupportOperationRequestModel,
    SupportSiteModel,
    TenantCaseMemoryModel,
    TenantPatternMemoryModel,
)

pytestmark = pytest.mark.integration


def test_customer_table_is_tenant_scoped() -> None:
    assert "tenant_id" in CustomerModel.__table__.columns
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in CustomerModel.__table__.constraints
        if hasattr(constraint, "columns")
    }
    assert ("tenant_id", "customer_id") in unique_columns


def test_support_operations_tables_are_tenant_scoped() -> None:
    for model in (SupportSiteModel, CustomerMemoryItemModel, SupportOperationRequestModel):
        assert "tenant_id" in model.__table__.columns


def test_platform_site_directory_is_sanitized_and_tenant_bound() -> None:
    columns = set(PlatformSiteDirectoryModel.__table__.columns.keys())
    assert {
        "tenant_id",
        "site_id",
        "name",
        "base_url",
        "status",
        "verification_status",
        "verification_expires_at",
        "knowledge_publication_state",
    }.issubset(columns)
    assert not {
        "site_key",
        "key_hash",
        "verification_token_hash",
        "install_code",
        "public_widget_id",
        "primary_language",
    }.intersection(columns)
    support_site_foreign_keys = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in SupportSiteModel.__table__.foreign_key_constraints
    }
    assert ("tenants.tenant_id",) in support_site_foreign_keys
    projection_foreign_keys = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in PlatformSiteDirectoryModel.__table__.foreign_key_constraints
    }
    assert ("support_sites.tenant_id", "support_sites.site_id") in projection_foreign_keys


def test_platform_tenant_entitlement_is_sanitized_and_tenant_bound() -> None:
    columns = set(PlatformTenantEntitlementModel.__table__.columns.keys())
    assert columns == {
        "tenant_id",
        "site_limit",
        "plan_id",
        "subscription_status",
        "quota_updated_at",
        "subscription_updated_at",
        "source_updated_at",
        "created_at",
        "updated_at",
    }
    foreign_keys = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in PlatformTenantEntitlementModel.__table__.foreign_key_constraints
    }
    assert ("tenants.tenant_id",) in foreign_keys


def test_conversation_tracks_independent_status_and_ownership() -> None:
    columns = ConversationModel.__table__.columns
    assert "status" in columns
    assert "ownership_mode" in columns
    assert "assigned_agent_id" in columns
    assert "site_id" in columns


def test_tenant_experience_control_plane_is_tenant_scoped_and_issue_level() -> None:
    for model in (
        IssueOutcomeEpisodeModel,
        TenantCaseMemoryModel,
        TenantPatternMemoryModel,
        ExperienceMemoryUsageModel,
        ExperienceExperimentModel,
        ExperienceEvalRunModel,
        ExperienceReleaseModel,
    ):
        assert "tenant_id" in model.__table__.columns
    assert "issue_id" in IssueOutcomeEpisodeModel.__table__.columns
    assert "issue_id" in ExperienceMemoryUsageModel.__table__.columns
    assert "outcome_attributed" in ExperienceMemoryUsageModel.__table__.columns
    assert "resolution_status" in IssueOutcomeEpisodeModel.__table__.columns
    assert "learning_outcome" in IssueOutcomeEpisodeModel.__table__.columns
    assert "actor_cohort_hash" in IssueOutcomeEpisodeModel.__table__.columns
    assert "source_actor_cohort_hash" in TenantCaseMemoryModel.__table__.columns
    assert "manifest" in ExperienceReleaseModel.__table__.columns
    assert TenantCaseMemoryModel.__table__.columns.embedding.type.dim == 384
