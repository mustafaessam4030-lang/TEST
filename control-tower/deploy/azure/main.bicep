// ATA Control Tower — Azure deployment.
//
//   App Service (Linux, Python 3.11)   dashboard + API + control plane
//   PostgreSQL Flexible Server          users, sessions, audit, runs, claims
//   Key Vault                           every secret; the app reads them by
//                                       managed identity (Key Vault references)
//   Log Analytics + Application Insights  telemetry and App Service logs
//   Windows VM (no public IP)           the ATA worker: Edge, Playwright, the
//                                       existing update_eta.py automation
//
// The worker makes OUTBOUND HTTPS calls to the App Service only. Its network
// security group allows no inbound traffic from the internet; administrators
// reach it through Azure Bastion (optional, deployBastion=true).
//
// NOT COMPILED IN THE ENVIRONMENT THAT WROTE IT: run `az bicep build --file
// main.bicep` first; fix any API-version drift it reports. See PLATFORM.md.

targetScope = 'resourceGroup'

@description('Short name used in every resource name, e.g. "ata".')
param prefix string = 'ata'
param location string = resourceGroup().location

@description('The address people will type, e.g. https://ata.mantrac.com')
param publicUrl string

@description('Entra ID tenant (directory) id. Leave empty to start with local accounts only.')
param entraTenantId string = ''
@description('Entra ID application (client) id for the ATA app registration.')
param entraClientId string = ''
@description('Comma-separated email domains allowed to sign in with Microsoft.')
param entraAllowedDomains string = 'mantrac.com'

@description('PostgreSQL administrator login.')
param pgAdminLogin string = 'ataadmin'
@secure()
@description('PostgreSQL administrator password (stored in Key Vault).')
param pgAdminPassword string

@description('Windows worker VM administrator.')
param vmAdminUser string = 'ataworker'
@secure()
param vmAdminPassword string
param vmSize string = 'Standard_D4s_v5'
param deployBastion bool = true

@description('App Service plan SKU. P1v3 keeps Always On and enough memory for the SSE streams.')
param appSku string = 'P1v3'

var name = toLower(prefix)
var suffix = uniqueString(resourceGroup().id)
var kvName = '${name}-kv-${substring(suffix, 0, 6)}'
var pgName = '${name}-pg-${substring(suffix, 0, 6)}'
var appName = '${name}-tower-${substring(suffix, 0, 6)}'

// ── Telemetry ──────────────────────────────────────────────────────────
resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${name}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 90 }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${name}-insights'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

// ── Secrets ────────────────────────────────────────────────────────────
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource pgPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'pg-admin-password'
  properties: { value: pgAdminPassword }
}

resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-url'
  properties: {
    value: 'postgresql://${pgAdminLogin}:${uriComponent(pgAdminPassword)}@${pg.properties.fullyQualifiedDomainName}:5432/ata?sslmode=require'
  }
}

// The Entra client secret is NOT created here: add it to the vault as
// "entra-client-secret" after registering the app (PLATFORM.md, step 4).

// ── Database ───────────────────────────────────────────────────────────
resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2022-12-01' = {
  name: pgName
  location: location
  sku: { name: 'Standard_B2s', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: pgAdminLogin
    administratorLoginPassword: pgAdminPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 14, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2022-12-01' = {
  parent: pg
  name: 'ata'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// Azure services (the App Service) may connect; nothing else.
resource pgAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: pg
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// ── Web app: dashboard + API + control plane ───────────────────────────
resource plan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: '${name}-plan'
  location: location
  kind: 'linux'
  sku: { name: appSku }
  properties: { reserved: true }
}

resource app 'Microsoft.Web/sites@2023-01-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: true          // one instance holds the live browser view
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'python -m controlplane serve'
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/healthz'
      webSocketsEnabled: false
      numberOfWorkers: 1                 // see PLATFORM.md: scale-out limitations
      appSettings: [
        { name: 'ATA_HOST', value: '0.0.0.0' }
        { name: 'ATA_PUBLIC_URL', value: publicUrl }
        { name: 'ATA_TRUST_PROXY', value: '1' }
        { name: 'ATA_LOCAL_LOGIN', value: empty(entraTenantId) ? '1' : '0' }
        { name: 'DATABASE_URL', value: '@Microsoft.KeyVault(SecretUri=${databaseUrlSecret.properties.secretUri})' }
        { name: 'ENTRA_TENANT_ID', value: entraTenantId }
        { name: 'ENTRA_CLIENT_ID', value: entraClientId }
        { name: 'ENTRA_CLIENT_SECRET', value: empty(entraTenantId) ? '' : '@Microsoft.KeyVault(VaultName=${kvName};SecretName=entra-client-secret)' }
        { name: 'ENTRA_ALLOWED_DOMAINS', value: entraAllowedDomains }
        { name: 'ATLAS_INTEL_DIR', value: '/home/ata/intelligence' }
        { name: 'ATLAS_DATA_ORIGIN', value: 'production' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        { name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '600' }
      ]
    }
  }
}

// The web app reads its secrets from the vault by its own identity.
resource appSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, app.id, 'secrets-user')
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6')   // Key Vault Secrets User
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource appLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-logs'
  scope: app
  properties: {
    workspaceId: logs.id
    logs: [
      { category: 'AppServiceHTTPLogs', enabled: true }
      { category: 'AppServiceConsoleLogs', enabled: true }
      { category: 'AppServiceAppLogs', enabled: true }
    ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}

// ── The Windows worker ─────────────────────────────────────────────────
resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: '${name}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: [ '10.40.0.0/16' ] }
    subnets: [
      { name: 'worker', properties: { addressPrefix: '10.40.1.0/24', networkSecurityGroup: { id: nsg.id } } }
      { name: 'AzureBastionSubnet', properties: { addressPrefix: '10.40.250.0/26' } }
    ]
  }
}

// No inbound from the internet. Outbound HTTPS is all the worker needs.
resource nsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${name}-worker-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'deny-internet-inbound'
        properties: {
          priority: 4000, direction: 'Inbound', access: 'Deny', protocol: '*'
          sourceAddressPrefix: 'Internet', sourcePortRange: '*'
          destinationAddressPrefix: '*', destinationPortRange: '*'
        }
      }
    ]
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-09-01' = {
  name: '${name}-worker-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: '${vnet.id}/subnets/worker' }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: '${name}-worker'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: 'ATAWORKER01'
      adminUsername: vmAdminUser
      adminPassword: vmAdminPassword
      windowsConfiguration: { enableAutomaticUpdates: true, provisionVMAgent: true }
    }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftWindowsDesktop', offer: 'windows-11', sku: 'win11-23h2-pro', version: 'latest'
      }
      osDisk: { createOption: 'FromImage', managedDisk: { storageAccountType: 'Premium_LRS' } }
    }
    networkProfile: { networkInterfaces: [ { id: nic.id } ] }
    securityProfile: { securityType: 'TrustedLaunch', uefiSettings: { secureBootEnabled: true, vTpmEnabled: true } }
  }
}

resource bastionIp 'Microsoft.Network/publicIPAddresses@2023-09-01' = if (deployBastion) {
  name: '${name}-bastion-ip'
  location: location
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource bastion 'Microsoft.Network/bastionHosts@2023-09-01' = if (deployBastion) {
  name: '${name}-bastion'
  location: location
  sku: { name: 'Basic' }
  properties: {
    ipConfigurations: [
      {
        name: 'bastion'
        properties: {
          subnet: { id: '${vnet.id}/subnets/AzureBastionSubnet' }
          publicIPAddress: { id: bastionIp.id }
        }
      }
    ]
  }
}

output appServiceName string = app.name
output appDefaultHost string = app.properties.defaultHostName
output keyVaultName string = vault.name
output postgresHost string = pg.properties.fullyQualifiedDomainName
output workerVm string = vm.name
