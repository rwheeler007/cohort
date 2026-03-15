# Incoming Email Project Tagging

## Automatic Project Detection

Incoming emails are automatically tagged with the correct project based on the **recipient email address** (the `to` field).

## Domain Mapping

The system maps email domains to projects:

| Domain | Project | Example |
|--------|---------|---------|
| `partspec.ai` | PartSpec.ai | `boss@partspec.ai`, `support@partspec.ai` |
| `chillguard.com` | ChillGuard | `contact@chillguard.com`, `support@chillguard.com` |
| `chillguard.io` | ChillGuard | `hello@chillguard.io` |

## How It Works

1. **Email arrives** via Resend webhook to `boss@partspec.ai`
2. **EmailReceiver** parses the webhook data
3. **Project detection** checks the `to` address domain
4. **Auto-tagging** adds `metadata: {"project": "partspec"}` to the email
5. **Dashboard display** shows PartSpec tag on the email card

## Configuration

Edit the `domain_to_project` mapping in `email_receiver.py`:

```python
# In EmailReceiver._detect_project_from_recipients()
domain_to_project = {
    "partspec.ai": "partspec",
    "chillguard.com": "chillguard",
    "chillguard.io": "chillguard",
    "yourcompany.com": "yourproject",  # Add new mappings here
}
```

## Adding New Project Email Addresses

### Step 1: Configure Domain Mapping

Add your domain to the mapping in [email_receiver.py](email_receiver.py):

```python
domain_to_project = {
    "partspec.ai": "partspec",
    "mynewcompany.com": "newproject",  # New!
}
```

### Step 2: Create the Project

Use the Communications Dashboard to add the project:

1. Go to `http://localhost:5000/comms`
2. Click **Settings** tab
3. Click **+ Add Project**
4. Fill in:
   - Project ID: `newproject`
   - Display Name: `My New Company`
   - Color: Choose a color
5. Click **Add Project**

### Step 3: Configure Resend Webhook

In Resend dashboard:

1. Add incoming email address: `boss@mynewcompany.com`
2. Point webhook to: `http://your-server:8001/api/email/webhook`
3. Send test email

## Result

All emails sent to `@partspec.ai` addresses will automatically:

- Be tagged with **PartSpec** project tag (orange)
- Appear in dashboard with PartSpec filter
- Be routed according to PartSpec-specific rules
- Store metadata: `{"project": "partspec"}`

## Example

**Email sent to**: `boss@partspec.ai`

**Webhook received**:
```json
{
  "type": "email.received",
  "data": {
    "from": "customer@example.com",
    "to": ["boss@partspec.ai"],
    "subject": "Question about pricing"
  }
}
```

**Stored email metadata**:
```json
{
  "project": "partspec"
}
```

**Dashboard display**:
```
[PartSpec.ai] Question about pricing
From: customer@example.com
Routed to: sales_agent
```

## Fallback Behavior

If no domain mapping matches:
- Email is still received and processed
- No project tag is added (`metadata: {}`)
- Email appears without a project tag in dashboard
- Can be manually tagged/routed later

## Testing

Send a test email to `boss@partspec.ai` and check:

1. **Dashboard** → Inbox tab → See PartSpec tag
2. **SMACK notification** → Shows project in routing message
3. **API query**: `GET /api/email/inbox/{email_id}` → Check `metadata.project`

---

**Status**: [OK] Production Ready
**Date**: 2026-02-04
