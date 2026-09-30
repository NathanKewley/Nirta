param location string
param storageName string
param containerName string
param skuName string

resource StorageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: skuName
  }
}

resource StorageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${StorageAccount.name}/default/${containerName}'
  properties: {
    publicAccess: 'None'
  }
}

output storageEndpoint object = StorageAccount.properties.primaryEndpoints
output storageLocation string = StorageAccount.properties.primaryLocation
