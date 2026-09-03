# Capital Campaign Console

Streamlit campaign management console backed by Microsoft SQL Server and SEGMA sync creation.

## Run

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Pages

The console uses Streamlit multipage routing:

- `streamlit_app.py`: all-channel campaign console.
- `pages/1_EDM_Campaign.py`: EDM-only campaign configuration.
- `pages/2_SMS_Campaign.py`: SMS-only campaign configuration.
- `pages/3_App_Notification_Campaign.py`: App notification-only campaign configuration.

## SEGMA API Configuration

Campaign creation inserts `marketing.campaign` in MSSQL, then posts a new sync to SEGMA:

```bash
export MSSQL_SERVER="sqlserver.example.com"
export MSSQL_PORT="1433"
export MSSQL_DATABASE="CampaignDb"
export MSSQL_USERNAME="campaign_user"
export MSSQL_PASSWORD="secret"
export SEGMA_API="https://segma.example.com"
export SEGMA_TOKEN="optional-bearer-token"
export SEGMA_USER_ID="7"
export SEGMA_CA_BUNDLE="/path/to/segma-ca.pem"
# Local troubleshooting only; prefer SEGMA_CA_BUNDLE for shared environments.
# export SEGMA_SSL_VERIFY="false"
export MSSQL_DESTINATION_ID="45"
export SEGMA_SYNC_DESTINATIONS_JSON='[{"id":45,"name":"Marketing MSSQL","type":"sql_server_table_sync"}]'
export SEGMA_SYNC_CHUNKSIZE="1000"
export SENDGRID_API_BASE_URL="https://api.sendgrid.com"
export SENDGRID_API_KEY="..."
export EDM_DEFAULT_SENDER_EMAIL="marketing@example.com"
export TWILIO_ACCOUNT_SID="..."
export TWILIO_AUTH_TOKEN="..."
export SMS_DEFAULT_SENDER_NUMBER="+15551234567"
export CAPITAL_MOBILE_AZURE_CONNECTION_STRING="Endpoint=sb://...;SharedAccessKeyName=...;SharedAccessKey=..."
streamlit run streamlit_app.py
```

See `.env.example` for the full environment variable list.

The console reads campaign lists/details from `marketing.campaign`, loads segments from SEGMA `GET /api/v1/segments`, loads optional seed lists from SEGMA ActionDatasets tagged `seed`, updates campaign cancellation in MSSQL, and calls `POST /api/v1/syncs` with `action_type: mssql_table` to ask SEGMA to populate the channel job table.

If SEGMA uses an internal CA or self-signed certificate, set `SEGMA_CA_BUNDLE` to a PEM file containing the CA certificate. `SEGMA_SSL_VERIFY=false` disables verification and should only be used temporarily for local troubleshooting.

## Runtime Behavior

- Campaign data is loaded from MSSQL.
- Segments are loaded from SEGMA `GET /api/v1/segments`; `SEGMA_SEGMENTS_JSON` can be used as fallback config. The selected segment is sent to `POST /api/v1/syncs` as `source_type: "Segment"`.
- Segment source fields are sent as `trait_id` sync columns. The console reads each selected segment's `dim_id`, loads traits from `GET /api/v1/traits?dim_id=<dim_id>&limit=1000`, and requires trait IDs for recipient fields (`PK_customer_id` plus `email`, `phone_number`, or APP identifiers by channel); generated campaign fields are sent as formulas.
- Sync destinations are loaded from SEGMA `GET /api/v1/destinations?q[action_type_eq]=mssql_table&limit=100&order_by=-created_at` and selected in the campaign form. `SEGMA_SYNC_DESTINATIONS_JSON` can be used as fallback config; `MSSQL_DESTINATION_ID` remains a one-option fallback.
- Seed list usage is optional per campaign. When enabled, seed lists are loaded from SEGMA `GET /api/v1/action_datasets?limit=100&offset=0`, and the console creates an additional SEGMA sync from the selected seed ActionDataset into the same campaign job table.
- EDM content is selected from SendGrid Dynamic Templates. The console lists templates through the SendGrid API, previews the active template version HTML, and sends test email with `template_id` plus `dynamic_template_data`.
- EDM Dynamic Template Data is configured as key/trait mappings in the UI; the console converts those mappings into a SEGMA formula that builds `dynamic_template_data_json` from selected segment traits.
- SEGMA sync scheduling is selected in the campaign form. The console sends daily/weekly/monthly/yearly cron expressions for recurring schedules, and for one-time schedules sends both the date cron plus `start_date` and `end_date` one minute apart. Linked syncs can also be manually triggered from the campaign detail page.
- Test sends call SendGrid, Twilio, or Azure Notification Hubs directly.
- Test sends accept a `personalization_json` sample. EDM merges `dynamic_template_data` and `custom_args`; SMS can override `message_body`; APP can override `notification.title`, `notification.body`, and `data`.
- Job rows are expected to be created by SEGMA in the channel job tables.
- Campaign cancellation deletes linked SEGMA main/seed syncs first, then updates MSSQL to `CANCELLED`; if SEGMA cleanup fails, the campaign status is left unchanged and the error is shown.
- Campaign deletion is allowed for any campaign status after typed-name confirmation; SEGMA sync cleanup is best-effort, and MSSQL campaign/job rows are deleted even if SEGMA cleanup fails.
- Campaign creation writes MSSQL and calls the SEGMA sync API with the selected segment as the main sync source.
- Campaign creation starts as `DRAFT`, changes to `ACTIVE` only after SEGMA sync creation is linked back to MSSQL, and changes to `SYNC_FAILED` if SEGMA sync creation/linking fails after the campaign row is inserted.
- `DRAFT`, `SYNC_FAILED`, `ACTIVE`, and `PAUSED` campaigns can be cancelled from the console; `COMPLETED` campaigns cannot.
- SEGMA must populate the job table with `campaign_id`, `customer_id`, recipient fields, content fields, `scheduled_for`, and `status = 'NEW'`.

APP campaign creation loads selectable applications from `marketing.app_notification_application`. Example registration:

```sql
INSERT INTO marketing.app_notification_application (
    app_id,
    app_name,
    platforms_json,
    azure_notification_hub_name,
    azure_connection_secret_name
)
VALUES (
    N'capital_mobile',
    N'Capital Mobile',
    N'["ios","android"]',
    N'capital-mobile-hub',
    N'CAPITAL_MOBILE_AZURE_CONNECTION_STRING'
);
```
