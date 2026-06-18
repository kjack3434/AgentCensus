"""Factual Dataverse / Copilot Studio interface constants.

These are public platform identifiers (entity/column names, picklist values,
channel ids) needed to read the API — not proprietary logic.
"""

from __future__ import annotations

API_VERSION = "v9.2"

DISCOVERY_URL = "https://globaldisco.crm.dynamics.com/api/discovery/v2.0/Instances"
DISCOVERY_SCOPE = "https://globaldisco.crm.dynamics.com/.default"

# Columns we read off the `bot` entity.
BOT_SELECT = (
    "botid,name,statecode,statuscode,createdon,modifiedon,publishedon,language,"
    "authenticationmode,accesscontrolpolicy,authorizedsecuritygroupids,configuration,"
    "_ownerid_value,template,runtimeprovider,origin,schemaname"
)

# Columns we read off the `botcomponent` entity.
BOTCOMPONENT_SELECT = (
    "botcomponentid,name,componenttype,_parentbotid_value,schemaname,statecode,data,description"
)

# componenttype picklist (botcomponent entity)
COMPONENT_BOT_DEFINITION = 15  # Custom GPT / bot definition: description, instructions, model
COMPONENT_KNOWLEDGE = 16  # knowledge source (search index, site, file, table)
COMPONENT_TOPIC = 9  # dialog topic (may reference external actions)
COMPONENT_EXTERNAL_TRIGGER = 17  # autonomous agent external trigger

# accesscontrolpolicy picklist (bot entity)
ACCESS_ANY = 0  # shared with everyone in the org
ACCESS_MULTI_TENANT = 3  # anyone from any tenant — highest exposure

# authenticationmode picklist (bot entity)
AUTH_NONE = 1  # no authentication required

# bot channel id -> display name
CHANNEL_DISPLAY: dict[str, str] = {
    "msteams": "Microsoft Teams",
    "microsoft365copilot": "Microsoft 365 Copilot",
    "webchat": "Web Chat",
    "directline": "Direct Line",
    "directlinespeech": "Direct Line Speech",
    "slack": "Slack",
    "facebook": "Facebook Messenger",
    "telegram": "Telegram",
    "twilio-sms": "Twilio SMS",
    "skype": "Skype",
    "kik": "Kik",
    "line": "LINE",
    "email": "Email",
    "groupme": "GroupMe",
    "sms": "SMS",
}

# modelNameHint -> display name (best-effort; falls back to the raw hint)
MODEL_HINT_DISPLAY: dict[str, str] = {
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
    "gpt-4-1": "GPT-4.1",
    "gpt-41": "GPT-4.1",
    "gpt-4": "GPT-4",
    "gpt-4-turbo": "GPT-4 Turbo",
    "gpt-35-turbo": "GPT-3.5 Turbo",
}
# Copilot Studio's current default GPT when no hint is present.
DEFAULT_MODEL_DISPLAY = "GPT-4.1"

# Action markers that can appear inside a topic component's `data`.
ACTION_MARKERS = {
    "InvokeExternalAgentTaskAction": ("mcp", "uses_mcp"),
    "HttpRequestAction": ("http", "uses_http_requests"),
    "InvokeConnectorTaskAction": ("connector_action", "uses_connector_actions"),
    "InvokeSkillAction": ("skill", "uses_skills"),
}
