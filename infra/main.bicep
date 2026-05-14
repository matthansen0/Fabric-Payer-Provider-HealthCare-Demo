@description('Azure region for all Azure resources. Keep aligned with Fabric workspace region.')
param location string = 'swedencentral'

@description('Resource group location tag for generated resources')
param resourceTags object = {
  project: 'fabric-healthcare-demo'
  managedBy: 'azd'
}

@description('Azure AI Foundry hub/account name (must be globally unique)')
param hubName string

@description('Foundry project name')
param projectName string = 'HealthcareDemo-HLS'

@description('Azure AI Search service name (must be globally unique)')
param searchServiceName string

@description('Fabric capacity resource name (must be globally unique in tenant context)')
param fabricCapacityName string

@description('Fabric capacity SKU')
@allowed([
  'F2'
  'F4'
  'F8'
  'F16'
  'F32'
  'F64'
  'F128'
  'F256'
  'F512'
  'F1024'
  'F2048'
])
param fabricCapacitySku string = 'F64'

@description('Fabric capacity admins as array (for example: ["admin@contoso.com"]). At least one value is required by Microsoft.Fabric/capacities.')
param fabricCapacityAdmins array

resource aiServices 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: hubName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  tags: resourceTags
  properties: {
    // Required to allow ARM-based Foundry Project creation under this hub.
    allowProjectManagement: true
    customSubDomainName: toLower(hubName)
    publicNetworkAccess: 'Enabled'
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: resourceTags
  properties: {
    disableLocalAuth: false
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
    networkRuleSet: {
      ipRules: []
    }
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'free'
  }
}

resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: fabricCapacityName
  location: location
  sku: {
    name: fabricCapacitySku
    tier: 'Fabric'
  }
  tags: resourceTags
  properties: {
    administration: {
      members: fabricCapacityAdmins
    }
  }
}

output LOCATION string = location
output HUB_NAME string = hubName
output PROJECT_NAME string = projectName
output AI_SERVICES_ID string = aiServices.id
output AI_SERVICES_NAME string = aiServices.name
output AI_SERVICES_PRINCIPAL_ID string = aiServices.identity.principalId
output SEARCH_SERVICE_NAME string = search.name
output SEARCH_SERVICE_ID string = search.id
output SEARCH_SERVICE_PRINCIPAL_ID string = search.identity.principalId
output FABRIC_CAPACITY_NAME string = fabricCapacity.name
output FABRIC_CAPACITY_ID string = fabricCapacity.id
