// SentinelAML production-scale architecture on Azure (NOT deployed during this build; see docs/deployment.md).
// Every resource is here because it solves a specific problem - see the comment above each one.
targetScope = 'resourceGroup'

@allowed(['staging', 'production'])
param environmentName string = 'staging'
param location string = resourceGroup().location
param apiImage string
@secure()
param postgresAdminPassword string
param postgresAdminLogin string = 'sentineladmin'

var prefix = 'sentinel-${environmentName}'
var isProd = environmentName == 'production'

// Log Analytics: central store for container logs, metrics and traces (Azure Monitor backend).
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: isProd ? 365 : 30 }
}

// Application Insights: receives OpenTelemetry traces (agent spans, LLM latency) from the API and workers.
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appi'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

// Container Registry: private, scanned image store; pulled via managed identity (no registry passwords).
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: replace('${prefix}acr', '-', '')
  location: location
  sku: { name: 'Premium' }
  properties: { adminUserEnabled: false, publicNetworkAccess: isProd ? 'Disabled' : 'Enabled' }
}

// Key Vault: JWT secret, DB password, Anthropic key. RBAC-only, purge protection, injected as container secrets.
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv'
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
  }
}

// PostgreSQL Flexible Server: transactional system of record + pgvector for retrieval (one engine to operate).
// Zone-redundant HA and 35-day PITR in production; data encrypted at rest.
resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${prefix}-pg'
  location: location
  sku: { name: isProd ? 'Standard_D4ds_v5' : 'Standard_B2ms', tier: isProd ? 'GeneralPurpose' : 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: isProd ? 512 : 64, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: isProd ? 35 : 7, geoRedundantBackup: isProd ? 'Enabled' : 'Disabled' }
    highAvailability: { mode: isProd ? 'ZoneRedundant' : 'Disabled' }
  }
}

resource pgExtensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: pg
  name: 'azure.extensions'
  properties: { value: 'VECTOR', source: 'user-override' }
}

// Blob Storage: model artifacts, raw batch drops and immutable (WORM) audit exports for 5-year retention.
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: replace('${prefix}st', '-', '')
  location: location
  sku: { name: isProd ? 'Standard_ZRS' : 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { allowBlobPublicAccess: false, minimumTlsVersion: 'TLS1_2', supportsHttpsTrafficOnly: true }
}

resource auditContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storage.name}/default/audit-export'
  properties: {
    immutableStorageWithVersioning: { enabled: true }
  }
}

// Service Bus: durable investigation queue (peek-lock, retries, dead-letter queue) between API and workers.
// Chosen over Event Hubs for this path because each message is a unit of work needing per-message ack/DLQ.
resource bus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${prefix}-sb'
  location: location
  sku: { name: 'Standard' }
}

resource investigationsQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: bus
  name: 'investigations'
  properties: { maxDeliveryCount: 5, lockDuration: 'PT5M', deadLetteringOnMessageExpiration: true, requiresDuplicateDetection: true }
}

// Event Hubs: high-throughput transaction stream from core banking (Kafka-compatible endpoint), replayable.
resource hubs 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: '${prefix}-eh'
  location: location
  sku: { name: 'Standard', capacity: isProd ? 4 : 1 }
  properties: { kafkaEnabled: true }
}

resource txHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: hubs
  name: 'transactions'
  properties: { partitionCount: isProd ? 32 : 4, messageRetentionInDays: 7 }
}

// Container Apps environment: managed Kubernetes-style runtime without operating a cluster; KEDA autoscaling.
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-cae'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
    zoneRedundant: isProd
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-api'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'http', allowInsecure: false }
      registries: [ { server: acr.properties.loginServer, identity: 'system' } ]
      secrets: [
        { name: 'jwt-secret', keyVaultUrl: '${kv.properties.vaultUri}secrets/jwt-secret', identity: 'system' }
        { name: 'database-url', keyVaultUrl: '${kv.properties.vaultUri}secrets/database-url', identity: 'system' }
        { name: 'anthropic-api-key', keyVaultUrl: '${kv.properties.vaultUri}secrets/anthropic-api-key', identity: 'system' }
      ]
    }
    template: {
      containers: [ {
        name: 'api'
        image: apiImage
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: [
          { name: 'ENVIRONMENT', value: 'production' }
          { name: 'LLM_PROVIDER', value: 'anthropic' }
          { name: 'JWT_SECRET', secretRef: 'jwt-secret' }
          { name: 'DATABASE_URL', secretRef: 'database-url' }
          { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
          { name: 'OTEL_ENABLED', value: 'true' }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        ]
        probes: [
          { type: 'Liveness', httpGet: { path: '/health/live', port: 8000 } }
          { type: 'Readiness', httpGet: { path: '/health/ready', port: 8000 } }
        ]
      } ]
      scale: { minReplicas: isProd ? 2 : 1, maxReplicas: 20, rules: [ { name: 'http', http: { metadata: { concurrentRequests: '50' } } } ] }
    }
  }
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-worker'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      registries: [ { server: acr.properties.loginServer, identity: 'system' } ]
      secrets: [
        { name: 'database-url', keyVaultUrl: '${kv.properties.vaultUri}secrets/database-url', identity: 'system' }
        { name: 'anthropic-api-key', keyVaultUrl: '${kv.properties.vaultUri}secrets/anthropic-api-key', identity: 'system' }
        { name: 'sb-connection', keyVaultUrl: '${kv.properties.vaultUri}secrets/servicebus-connection', identity: 'system' }
      ]
    }
    template: {
      containers: [ {
        name: 'worker'
        image: apiImage
        command: [ 'python', '-m', 'app.worker' ]
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: [
          { name: 'ENVIRONMENT', value: 'production' }
          { name: 'LLM_PROVIDER', value: 'anthropic' }
          { name: 'DATABASE_URL', secretRef: 'database-url' }
          { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
        ]
      } ]
      // KEDA scales workers on queue depth: backpressure is absorbed by the queue, not by the API.
      scale: { minReplicas: 0, maxReplicas: 30, rules: [ {
        name: 'queue-depth'
        custom: { type: 'azure-servicebus', metadata: { queueName: 'investigations', messageCount: '10' }, auth: [ { secretRef: 'sb-connection', triggerParameter: 'connection' } ] }
      } ] }
    }
  }
}

output apiFqdn string = api.properties.configuration.ingress.fqdn
