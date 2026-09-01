export interface DamIdentity {
  damId: string | null
  name: string | null
  river: string | null
  basin: string | null
  state: string | null
  district: string | null
  latitude: number | null
  longitude: number | null
}

export interface DamStructuralData {
  height: number | null
  length: number | null
  damType: string | null
  foundationType: string | null
  crestElevation: number | null
  spillwayCapacity: number | null
  gateInformation: string | null
  constructionYear: number | null
  inspectionInformation: string | null
}

export interface DamReservoirData {
  grossStorage: number | null
  liveStorage: number | null
  deadStorage: number | null
  currentLevel: number | null
  frl: number | null
  mwl: number | null
  inflow: number | null
  outflow: number | null
}

export interface DamHydrologyData {
  rainfall: number | null
  riverDischarge: number | null
  upstreamInflow: number | null
  forecastRainfall: number | null
  historicalExtremes: string | null
}

export interface DamTerrainData {
  dem: string | null
  slope: number | null
  elevation: number | null
  riverNetwork: string | null
  landCover: string | null
  downstreamDistance: number | null
}

export interface DamEnvironmentData {
  airQuality: number | null
  waterQuality: string | null
  temperature: number | null
  weather: string | null
  soilGeology: string | null
}

export interface DamExposureData {
  population: number | null
  buildings: number | null
  roads: number | null
  bridges: number | null
  hospitals: number | null
  schools: number | null
  criticalInfrastructure: number | null
}

export interface DamMonitoringData {
  sensors: string | null
  remoteSensing: string | null
  satelliteObservations: string | null
  historicalIncidents: string | null
  inspectionRecords: string | null
}

export interface DamDigitalTwin {
  identity: DamIdentity
  structural: DamStructuralData
  reservoir: DamReservoirData
  hydrology: DamHydrologyData
  terrain: DamTerrainData
  environment: DamEnvironmentData
  exposure: DamExposureData
  monitoring: DamMonitoringData
}