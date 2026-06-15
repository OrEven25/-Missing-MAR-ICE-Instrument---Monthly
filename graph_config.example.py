# Copy this file to graph_config.py and fill in your values.
# Never commit graph_config.py — it is listed in .gitignore.
#
# SETUP STEPS (do this once in https://portal.azure.com):
#
#  1. Go to Azure Active Directory → App registrations → New registration
#     - Name: "MAR ICE Outlook Agent"
#     - Supported account types: "Accounts in this organizational directory only"
#     - Click Register
#
#  2. Copy the values into graph_config.py:
#     - Application (client) ID  → CLIENT_ID
#     - Directory (tenant) ID    → TENANT_ID
#
#  3. Go to API permissions → Add a permission → Microsoft Graph
#     → Delegated permissions → add:
#       • Mail.Read
#       • Mail.Send
#     Grant consent for your organisation.

TENANT_ID  = "your-tenant-id-here"
CLIENT_ID  = "your-client-id-here"

# The mailbox to read from and send as
USER_EMAIL = "your.email@yourorg.com"
