"""Which MCP tools a credential may call: the admin set and the access checks."""

from __future__ import annotations

import logging

from homeassistant.exceptions import Unauthorized

from ..const import (
    SELORA_JWT_WRITE_SCOPE,
)
from ..selora_auth import SeloraAuthContext
from .names import (
    TOOL_ACCEPT_AUTOMATION,
    TOOL_ACCEPT_SUGGESTION,
    TOOL_ACTIVATE_SCENE,
    TOOL_ADD_DASHBOARD_RESOURCE,
    TOOL_ADD_DASHBOARD_VIEW,
    TOOL_ASSIGN_AREA,
    TOOL_ASSIGN_CATEGORY,
    TOOL_ASSIGN_LABELS,
    TOOL_CHAT,
    TOOL_CREATE_AREA,
    TOOL_CREATE_AUTOMATION,
    TOOL_CREATE_CATEGORY,
    TOOL_CREATE_DASHBOARD,
    TOOL_CREATE_FLOOR,
    TOOL_CREATE_GROUP,
    TOOL_CREATE_HELPER,
    TOOL_CREATE_LABEL,
    TOOL_CREATE_SCENE,
    TOOL_DELETE_AREA,
    TOOL_DELETE_AUTOMATION,
    TOOL_DELETE_CALENDAR_EVENT,
    TOOL_DELETE_CATEGORY,
    TOOL_DELETE_DASHBOARD,
    TOOL_DELETE_FILE,
    TOOL_DELETE_FLOOR,
    TOOL_DELETE_GROUP,
    TOOL_DELETE_HELPER,
    TOOL_DELETE_LABEL,
    TOOL_DELETE_SCENE,
    TOOL_DELETE_SCRIPT,
    TOOL_DISMISS_SUGGESTION,
    TOOL_EVAL_TEMPLATE,
    TOOL_EXECUTE_COMMAND,
    TOOL_FIND_ENTITIES_BY_AREA,
    TOOL_FIND_REFERENCES,
    TOOL_GET_AUTOMATION,
    TOOL_GET_AUTOMATION_TRACES,
    TOOL_GET_BLUEPRINT,
    TOOL_GET_CAMERA_IMAGE,
    TOOL_GET_CONFIG_YAML,
    TOOL_GET_DASHBOARD,
    TOOL_GET_DASHBOARD_CARD,
    TOOL_GET_DEVICE,
    TOOL_GET_DEVICE_TRIGGERS,
    TOOL_GET_ENTITY_HISTORY,
    TOOL_GET_ENTITY_STATE,
    TOOL_GET_HOME_SNAPSHOT,
    TOOL_GET_LOGS,
    TOOL_GET_PATTERN,
    TOOL_GET_SCENE,
    TOOL_GET_SCRIPT,
    TOOL_GROUP_DASHBOARD_CARDS,
    TOOL_HACS_ADD_REPOSITORY,
    TOOL_HACS_INFO,
    TOOL_HACS_INSTALL,
    TOOL_HACS_REMOVE,
    TOOL_HACS_SEARCH,
    TOOL_HOME_ANALYTICS,
    TOOL_INSERT_DASHBOARD_CARD,
    TOOL_LIST_AREAS,
    TOOL_LIST_AUTOMATIONS,
    TOOL_LIST_BLUEPRINTS,
    TOOL_LIST_CALENDAR_EVENTS,
    TOOL_LIST_CATEGORIES,
    TOOL_LIST_DASHBOARD_RESOURCES,
    TOOL_LIST_DASHBOARDS,
    TOOL_LIST_DEVICES,
    TOOL_LIST_FILES,
    TOOL_LIST_FLOORS,
    TOOL_LIST_GROUPS,
    TOOL_LIST_HELPERS,
    TOOL_LIST_LABELS,
    TOOL_LIST_PATTERNS,
    TOOL_LIST_SCENES,
    TOOL_LIST_SCRIPTS,
    TOOL_LIST_SERVICES,
    TOOL_LIST_SESSIONS,
    TOOL_LIST_SUGGESTIONS,
    TOOL_MOVE_DASHBOARD_CARD,
    TOOL_READ_FILE,
    TOOL_REMOVE_DASHBOARD_CARD,
    TOOL_REMOVE_DASHBOARD_RESOURCE,
    TOOL_REMOVE_DASHBOARD_VIEW,
    TOOL_SEARCH_ENTITIES,
    TOOL_SET_CALENDAR_EVENT,
    TOOL_SET_CONFIG_YAML,
    TOOL_SET_SCRIPT,
    TOOL_TRIGGER_AUTOMATION,
    TOOL_TRIGGER_SCAN,
    TOOL_UPDATE_AREA,
    TOOL_UPDATE_DASHBOARD,
    TOOL_UPDATE_DASHBOARD_CARD,
    TOOL_UPDATE_DASHBOARD_VIEW,
    TOOL_UPDATE_DEVICE,
    TOOL_UPDATE_ENTITY,
    TOOL_UPDATE_FLOOR,
    TOOL_UPDATE_GROUP,
    TOOL_UPDATE_HELPER,
    TOOL_VALIDATE_ACTION,
    TOOL_VALIDATE_AUTOMATION,
    TOOL_VALIDATE_SCENE,
    TOOL_WRITE_FILE,
)

_LOGGER = logging.getLogger(__name__)


# Tools that require admin / write scope: mutating operations plus
# eval_template, which exposes HA's full Jinja engine — broad state
# enumeration and non-trivial CPU cost make it too powerful for a
# read-only credential.
_ADMIN_TOOLS = frozenset(
    {
        TOOL_CREATE_HELPER,
        TOOL_UPDATE_HELPER,
        TOOL_DELETE_HELPER,
        TOOL_CREATE_AUTOMATION,
        TOOL_ACCEPT_AUTOMATION,
        TOOL_DELETE_AUTOMATION,
        TOOL_TRIGGER_AUTOMATION,
        TOOL_CHAT,
        TOOL_ACCEPT_SUGGESTION,
        TOOL_DISMISS_SUGGESTION,
        TOOL_TRIGGER_SCAN,
        TOOL_CREATE_SCENE,
        TOOL_DELETE_SCENE,
        TOOL_ACTIVATE_SCENE,
        TOOL_EXECUTE_COMMAND,
        TOOL_EVAL_TEMPLATE,
        TOOL_CREATE_GROUP,
        TOOL_UPDATE_GROUP,
        TOOL_DELETE_GROUP,
        TOOL_ASSIGN_AREA,
        TOOL_CREATE_AREA,
        TOOL_UPDATE_AREA,
        TOOL_DELETE_AREA,
        # Read-only, but admin-gated to match Home Assistant: it decorates
        # `blueprint/list` with `require_admin`. A blueprint is an automation
        # template with its author's source URL and input defaults — config
        # detail HA does not show a non-admin, so exposing it here would put
        # this tool surface below HA's own authorization boundary.
        TOOL_LIST_BLUEPRINTS,
        TOOL_GET_BLUEPRINT,
        TOOL_CREATE_CATEGORY,
        TOOL_ASSIGN_CATEGORY,
        TOOL_DELETE_CATEGORY,
        TOOL_CREATE_FLOOR,
        TOOL_UPDATE_FLOOR,
        TOOL_DELETE_FLOOR,
        TOOL_UPDATE_ENTITY,
        TOOL_UPDATE_DEVICE,
        TOOL_SET_SCRIPT,
        TOOL_DELETE_SCRIPT,
        TOOL_CREATE_LABEL,
        TOOL_ASSIGN_LABELS,
        TOOL_DELETE_LABEL,
        TOOL_INSERT_DASHBOARD_CARD,
        TOOL_ADD_DASHBOARD_VIEW,
        TOOL_UPDATE_DASHBOARD_VIEW,
        TOOL_REMOVE_DASHBOARD_VIEW,
        TOOL_UPDATE_DASHBOARD_CARD,
        TOOL_REMOVE_DASHBOARD_CARD,
        TOOL_MOVE_DASHBOARD_CARD,
        TOOL_GROUP_DASHBOARD_CARDS,
        TOOL_CREATE_DASHBOARD,
        TOOL_DELETE_DASHBOARD,
        TOOL_UPDATE_DASHBOARD,
        TOOL_ADD_DASHBOARD_RESOURCE,
        TOOL_REMOVE_DASHBOARD_RESOURCE,
        TOOL_SET_CONFIG_YAML,
        # Read-only, but admin-gated: configuration.yaml carries the home's
        # integration settings, and inline credentials are masked by pattern,
        # which is a best effort rather than a boundary.
        TOOL_GET_CONFIG_YAML,
        TOOL_WRITE_FILE,
        TOOL_DELETE_FILE,
        TOOL_HACS_INSTALL,
        TOOL_HACS_REMOVE,
        TOOL_HACS_ADD_REPOSITORY,
        # Read-only, but admin-gated to match HACS, whose commands are all
        # require_admin.
        TOOL_HACS_SEARCH,
        TOOL_HACS_INFO,
        # Read-only, but admin-gated like configuration YAML: they reach the
        # config folder, and what sits in www/ or custom_templates/ is the
        # user's own.
        TOOL_LIST_FILES,
        TOOL_READ_FILE,
        # Read-only, but admin-gated to match Home Assistant: it guards both
        # ``system_log/list`` and every ``trace/*`` command with
        # ``require_admin``. Logs carry exception text and configuration
        # details, and traces expose what automations do and when they fire —
        # neither is something a read-only credential should be able to mine.
        TOOL_GET_LOGS,
        TOOL_GET_AUTOMATION_TRACES,
        # Read-only too, but a camera shows the inside of the home — more than
        # its states say — and a read-only credential is the one most often
        # handed to an outside assistant.
        TOOL_GET_CAMERA_IMAGE,
        TOOL_SET_CALENDAR_EVENT,
        TOOL_DELETE_CALENDAR_EVENT,
    }
)

# All read-only tools (complement of _ADMIN_TOOLS)
_READ_ONLY_TOOLS = frozenset(
    {
        TOOL_LIST_AUTOMATIONS,
        TOOL_GET_AUTOMATION,
        TOOL_VALIDATE_AUTOMATION,
        TOOL_GET_HOME_SNAPSHOT,
        TOOL_LIST_SESSIONS,
        TOOL_LIST_PATTERNS,
        TOOL_GET_PATTERN,
        TOOL_LIST_SUGGESTIONS,
        TOOL_LIST_DEVICES,
        TOOL_GET_DEVICE,
        TOOL_GET_DEVICE_TRIGGERS,
        TOOL_GET_ENTITY_STATE,
        TOOL_FIND_ENTITIES_BY_AREA,
        TOOL_VALIDATE_ACTION,
        TOOL_SEARCH_ENTITIES,
        TOOL_GET_ENTITY_HISTORY,
        TOOL_HOME_ANALYTICS,
        TOOL_LIST_SCENES,
        TOOL_GET_SCENE,
        TOOL_VALIDATE_SCENE,
        TOOL_LIST_GROUPS,
        TOOL_LIST_AREAS,
        TOOL_LIST_SERVICES,
        TOOL_LIST_SCRIPTS,
        TOOL_GET_SCRIPT,
        TOOL_FIND_REFERENCES,
        TOOL_LIST_LABELS,
        TOOL_LIST_HELPERS,
        TOOL_GET_DASHBOARD,
        TOOL_LIST_CATEGORIES,
        TOOL_LIST_FLOORS,
        TOOL_LIST_DASHBOARDS,
        TOOL_GET_DASHBOARD_CARD,
        TOOL_LIST_DASHBOARD_RESOURCES,
        TOOL_LIST_CALENDAR_EVENTS,
    }
)


def _jwt_has_write(auth_ctx: SeloraAuthContext) -> bool:
    """Selora JWT carries write capability via the ``mcp:write`` scope."""
    return SELORA_JWT_WRITE_SCOPE in auth_ctx.scopes


def _check_tool_access(auth_ctx: SeloraAuthContext, tool_name: str) -> None:
    """Raise Unauthorized if *auth_ctx* does not grant access to *tool_name*.

    For MCP tokens with ``allowed_tools`` set, only those tools are accessible.
    For MCP tokens with ``read_only`` permission, only read-only tools are accessible.
    For Selora JWTs, ``_ADMIN_TOOLS`` require either an admin role or the
    ``mcp:write`` OAuth scope.
    For HA tokens, the existing is_admin check applies.
    """
    if auth_ctx.auth_type == "mcp_token":
        if auth_ctx.allowed_tools is not None:
            # Custom permission: explicit tool allowlist
            if tool_name not in auth_ctx.allowed_tools:
                _LOGGER.warning(
                    "MCP token %s allowlist denies tool %s",
                    auth_ctx.token_id,
                    tool_name,
                )
                raise Unauthorized
            return
        if not auth_ctx.is_admin and tool_name in _ADMIN_TOOLS:
            _LOGGER.warning(
                "Read-only MCP token %s refused tool %s",
                auth_ctx.token_id,
                tool_name,
            )
            raise Unauthorized
        return

    if auth_ctx.auth_type == "selora_jwt":
        if tool_name in _ADMIN_TOOLS and not (auth_ctx.is_admin or _jwt_has_write(auth_ctx)):
            _LOGGER.warning(
                "Selora JWT %s lacks '%s' scope; refused tool %s",
                auth_ctx.user_id,
                SELORA_JWT_WRITE_SCOPE,
                tool_name,
            )
            raise Unauthorized
        return

    # HA token: binary admin check
    if tool_name in _ADMIN_TOOLS and not auth_ctx.is_admin:
        _LOGGER.warning(
            "HA user %s lacks admin; refused tool %s",
            auth_ctx.user_id,
            tool_name,
        )
        raise Unauthorized


def _can_access_tool(auth_ctx: SeloraAuthContext, tool_name: str) -> bool:
    """Return True if *auth_ctx* grants access to *tool_name*."""
    if auth_ctx.auth_type == "mcp_token":
        if auth_ctx.allowed_tools is not None:
            return tool_name in auth_ctx.allowed_tools
        return auth_ctx.is_admin or tool_name not in _ADMIN_TOOLS
    if auth_ctx.auth_type == "selora_jwt":
        if tool_name not in _ADMIN_TOOLS:
            return True
        return auth_ctx.is_admin or _jwt_has_write(auth_ctx)
    return auth_ctx.is_admin or tool_name not in _ADMIN_TOOLS
