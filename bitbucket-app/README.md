# CodeCrow Bitbucket Connect App

This directory contains the Bitbucket Connect App descriptor and related configuration for workspace-based integration with Bitbucket Cloud.

## Overview

The Bitbucket Connect App provides workspace-level integration with Bitbucket Cloud, as opposed to user-level OAuth. This is the recommended approach for SaaS applications.

### Benefits over OAuth Consumer

| Aspect | OAuth Consumer | Connect App |
|--------|---------------|-------------|
| **Installation** | Per-user authorization | Per-workspace installation |
| **Token management** | Manual refresh token handling | JWT-based, Bitbucket manages |
| **Webhook setup** | Manual via API per repo | Automatic via descriptor |
| **UI Integration** | None | Can add buttons, pages in Bitbucket |
| **Marketplace** | Not listable | Can be listed in Atlassian Marketplace |
| **Multi-user** | Each user authorizes | Install once, all workspace members have access |
| **Uninstall** | Manual cleanup | Automatic lifecycle hooks |

## Files

- `atlassian-connect.json` - The Connect App descriptor
- `README.md` - This file

## Setup Instructions

### 1. Create Bitbucket App

1. Go to [Bitbucket Developer Console](https://developer.atlassian.com/console/myapps/)
2. Click **Create** → **Bitbucket Cloud App**
3. Fill in app details:
   - **Name**: CodeCrow
   - **Description**: AI-Powered Code Review Platform
   - **Vendor name**: Your company name
   - **Vendor URL**: Your website

### 2. Configure App Settings

In the app settings:

#### Permissions (Scopes)
Enable these scopes:
- `account` - Read user account info
- `repository` - Read repositories
- `repository:write` - Write to repositories (for webhooks)
- `pullrequest` - Read pull requests
- `pullrequest:write` - Comment on pull requests
- `webhook` - Manage webhooks

#### Callback URL
Set the callback URL to:
```
https://your-codecrow-domain.com/api/bitbucket/connect/callback
```

#### Install Callback URL
```
https://your-codecrow-domain.com/api/bitbucket/connect/installed
```

#### Uninstall Callback URL
```
https://your-codecrow-domain.com/api/bitbucket/connect/uninstalled
```

### 3. Get Credentials

After creating the app, note down:
- **Client ID** (also called Key)
- **Client Secret**

### 4. Configure CodeCrow

Add to your `application.properties` or environment variables:

```properties
# Bitbucket Connect App Configuration
codecrow.bitbucket.connect.client-id=YOUR_CLIENT_ID
codecrow.bitbucket.connect.client-secret=YOUR_CLIENT_SECRET
codecrow.bitbucket.connect.descriptor-url=https://your-domain.com/api/bitbucket/connect/descriptor
```

### 5. Deploy Descriptor

The Connect descriptor must be publicly accessible at:
```
https://your-domain.com/api/bitbucket/connect/descriptor
```

CodeCrow backend serves this automatically from the `/api/bitbucket/connect/descriptor` endpoint.

### 6. Install the App

#### Development/Testing
Use the development installation URL:
```
https://bitbucket.org/site/addons/authorize?addon_key=codecrow-connect-app
```

Or install via workspace settings:
1. Go to your Bitbucket workspace
2. Settings → Installed apps → Install app from URL
3. Enter: `https://your-domain.com/api/bitbucket/connect/descriptor`

#### Production (Marketplace)
For production, list your app on the [Atlassian Marketplace](https://marketplace.atlassian.com/):
1. Submit your app for review
2. Once approved, users can install directly from the Marketplace

## How It Works

### Installation Flow

1. User clicks "Install" in Bitbucket or Marketplace
2. Bitbucket fetches the descriptor from CodeCrow
3. User authorizes the app permissions
4. Bitbucket calls the `installed` lifecycle callback
5. CodeCrow stores the installation (clientKey, sharedSecret, principal)
6. Webhooks are automatically registered based on descriptor

### Authentication

All API calls from Bitbucket include a JWT token:
```
Authorization: JWT <token>
```

CodeCrow validates this token using the shared secret from the installation.

### Webhooks

Webhooks are defined in the descriptor and automatically registered when the app is installed on a repository. No manual webhook setup required.

Events supported:
- `repo:push` - Branch pushes
- `pullrequest:created` - PR created
- `pullrequest:updated` - PR updated (new commits, description changes)
- `pullrequest:fulfilled` - PR merged
- `pullrequest:rejected` - PR declined

### API Access

The app can access Bitbucket API on behalf of the workspace:

```java
// Get access token for API calls
String accessToken = bitbucketConnectService.getAccessToken(installation);

// Make API calls
BitbucketClient client = new BitbucketClient(accessToken);
List<Repository> repos = client.listRepositories(workspaceSlug);
```

## Troubleshooting

### "App descriptor not found"
- Ensure the descriptor URL is publicly accessible
- Check that the endpoint returns valid JSON with correct Content-Type

### "Installation failed"
- Check CodeCrow logs for the lifecycle callback
- Verify the callback URLs are correct and accessible
- Ensure JWT validation is working

### "Webhooks not firing"
- Check that the app has the `webhook` scope
- Verify webhook URLs in the descriptor are correct
- Check Bitbucket webhook settings in repository

## Development

### Local Testing

For local development, use a tunnel like ngrok:
```bash
ngrok http 8081
```

Update your descriptor with the ngrok URL:
```json
{
  "baseUrl": "https://abc123.ngrok.io"
}
```

### Descriptor Validation

Validate your descriptor at:
https://developer.atlassian.com/cloud/bitbucket/connect-app-descriptor-validator/

## References

- [Bitbucket Connect Documentation](https://developer.atlassian.com/cloud/bitbucket/connect/)
- [Connect App Descriptor](https://developer.atlassian.com/cloud/bitbucket/connect-app-descriptor/)
- [Authentication](https://developer.atlassian.com/cloud/bitbucket/connect-app-authentication/)
- [Webhooks](https://developer.atlassian.com/cloud/bitbucket/connect-app-webhooks/)
