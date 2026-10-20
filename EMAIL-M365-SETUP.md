# IT Projects – email through Microsoft 365: setup for the tenant administrator

IT Projects sends two kinds of email (a daily reminder, and "a task was assigned to you").
It sends them through **Microsoft Graph** with an **app-only** identity: no person signs in, and the
application can send **only as one mailbox you choose**. Nothing else in the tenant is reachable.
You need the Entra role *Application Administrator* (or higher) and the Exchange role *Organization Management*.

You will hand four values to the infrastructure team (section 6): tenant id, client id, client secret, sender address.

## 1. The sending mailbox
Create a mailbox for the application, for example `it-projects@company.mr` (a **shared mailbox** is enough: no licence needed).
Emails will show this mailbox's display name. Replies go to it, so someone should read it, or set a reply rule.

## 2. Register the application (Microsoft Entra admin center)
1. *Identity → Applications → App registrations → New registration.* Name: `IT Projects mailer`. Account type: **this organizational directory only**. No redirect URI.
2. On the *Overview* page copy **Application (client) ID** and **Directory (tenant) ID**.

## 3. Create the client secret
*Certificates & secrets → New client secret.* Choose a lifetime (maximum 24 months) and **put a reminder in a calendar for its expiry date**:
when it expires, emails stop (the application logs `invalid_client`). Copy the secret **Value** immediately (it is shown once).
Give it to the infrastructure team through your secret store. Never by chat or ordinary email, never in git.

## 4. Allow sending, for this one mailbox only (RBAC for Applications)
Microsoft's current recommended method, which replaces Application Access Policies
([Microsoft documentation](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access)). In Exchange Online PowerShell:

```powershell
# a) mark the mailbox, and define a scope that contains only it
Set-Mailbox it-projects@company.mr -CustomAttribute1 "ITPM"
New-ManagementScope -Name "ITPM mailbox" -RecipientRestrictionFilter "CustomAttribute1 -eq 'ITPM'"

# b) tell Exchange about the application (ids: Entra → Enterprise applications → IT Projects mailer;
#    the ObjectId is the ENTERPRISE application's object id, not the app registration's)
New-ServicePrincipal -AppId <Application (client) ID> -ObjectId <Enterprise application object ID> -DisplayName "IT Projects mailer"

# c) allow "Application Mail.Send", limited to that scope
New-ManagementRoleAssignment -App "IT Projects mailer" -Role "Application Mail.Send" -CustomResourceScope "ITPM mailbox"

# d) check (this test ignores the delay below)
Test-ServicePrincipalAuthorization -Identity "IT Projects mailer" -Resource it-projects@company.mr | Format-Table
```

**Important (from Microsoft's page):** do **not** leave an organization-wide `Mail.Send` permission with admin consent on this application
in Entra. If one exists, remove it: otherwise it overrides the limit and the application could send as **any** mailbox.
Changes can take **30 minutes to 2 hours** to apply.

## 5. Check
1. From the application: `python manage.py send_test_email you@company.mr` → "Test email accepted".
2. **Negative check (please do it):** the application must *not* be able to send as someone else. With a copy of the settings
   where `GRAPH_SENDER` is another mailbox, `send_test_email` must fail with `403 ErrorAccessDenied`.

## 6. What to give the infrastructure team
| Setting (environment variable) | Value |
|---|---|
| `EMAIL_BACKEND` | `config.graph_email.GraphEmailBackend` |
| `GRAPH_TENANT_ID` | Directory (tenant) ID |
| `GRAPH_CLIENT_ID` | Application (client) ID |
| `GRAPH_CLIENT_SECRET` | the secret **Value** |
| `GRAPH_SENDER` | `it-projects@company.mr` |
| `DEFAULT_FROM_EMAIL` | `IT Projects <it-projects@company.mr>` (informational: Microsoft sends as the mailbox) |
| `GRAPH_SAVE_TO_SENT_ITEMS` | optional, `True` keeps a copy of every email in the mailbox's Sent Items (default `False`) |

## 7. If something fails (the message is in the application's log and in `send_test_email`)
| Message | Meaning |
|---|---|
| `Could not get an access token: HTTP 401 invalid_client` | wrong or expired client secret, or wrong client id |
| `... HTTP 400 ... AADSTS90002` / tenant not found | wrong tenant id |
| `HTTP 403 ErrorAccessDenied` | the role assignment is missing or not applied yet (wait up to 2 hours), the sender is outside the scope, or the app has no `Mail.Send` |
| `HTTP 404 ErrorInvalidUser` / `MailboxNotFound` | `GRAPH_SENDER` is not an existing mailbox |
| `HTTP 429` | too many emails in a short time: Exchange Online allows about 30 per minute per mailbox; the application waits and retries once |
| `Could not reach Microsoft` | the container cannot reach `login.microsoftonline.com` / `graph.microsoft.com` on port 443 (firewall or proxy) |

The application never writes the secret in its logs. Task titles appear in the emails, so they pass through Microsoft 365.

## 8. Honest status
The application side is tested against a simulated Microsoft service (sign-in, token reuse and renewal, sending, retries, refusals).
It has **not** been run against a real tenant by the developer: the steps above follow Microsoft's documentation and should be validated once
with the checks in section 5.
