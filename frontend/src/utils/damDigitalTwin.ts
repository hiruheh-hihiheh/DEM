import type { DamFeature } from '../types/dam'
import type {
  DamDigitalTwin,
  DamEnvironmentData,
  DamExposureData,
  DamHydrologyData,
  DamMonitoringData,
  DamReservoirData,
  DamStructuralData,
  DamTerrainData,
} from '../types/digitalTwin'

function buildIdentity(
  dam: DamFeature,
): DamDigitalTwin['identity'] {
  const [longitude, latitude] =
    dam.geometry.coordinates

  return {
    damId:
      dam.properties.pic ??
      dam.id ??
      null,

    name:
      dam.properties.name ??
      null,

    river:
      dam.properties.river ??
      null,

    basin:
      dam.properties.basin ??
      null,

    state:
      dam.properties.state ??
      null,

    district:
      dam.properties.district ??
      null,

    latitude:
      Number.isFinite(latitude)
        ? latitude
        : null,

    longitude:
      Number.isFinite(longitude)
        ? longitude
        : null,
  }
}

function buildStructural(
  dam: DamFeature,
): DamStructuralData {
  return {
    height:
      dam.properties.height ??
      null,

    length:
      dam.properties.dam_length ??
      null,

    damType:
      dam.properties.dam_type ??
      null,

    foundationType: null,

    crestElevation: null,

    spillwayCapacity: null,

    gateInformation: null,

    constructionYear:
      dam.properties.completion_year ??
      null,

    inspectionInformation: null,
  }
}

function buildReservoir(
  dam: DamFeature,
): DamReservoirData {
  return {
    grossStorage:
      dam.properties.gross_storage_capacity ??
      null,

    liveStorage: null,

    deadStorage:
      dam.properties.dead_storage_capacity ??
      null,

    currentLevel: null,

    frl:
      dam.properties.full_reservoir_level ??
      null,

    mwl:
      dam.properties.max_water_level ??
      null,

    inflow: null,

    outflow: null,
  }
}

function buildHydrology(): DamHydrologyData {
  return {
    rainfall: null,
    riverDischarge: null,
    upstreamInflow: null,
    forecastRainfall: null,
    historicalExtremes: null,
  }
}

function buildTerrain(): DamTerrainData {
  return {
    dem: null,
    slope: null,
    elevation: null,
    riverNetwork: null,
    landCover: null,
    downstreamDistance: null,
  }
}

function buildEnvironment(): DamEnvironmentData {
  return {
    airQuality: null,
    waterQuality: null,
    temperature: null,
    weather: null,
    soilGeology: null,
  }
}

function buildExposure(): DamExposureData {
  return {
    population: null,
    buildings: null,
    roads: null,
    bridges: null,
    hospitals: null,
    schools: null,
    criticalInfrastructure: null,
  }
}

function buildMonitoring(): DamMonitoringData {
  return {
    sensors: null,
    remoteSensing: null,
    satelliteObservations: null,
    historicalIncidents: null,
    inspectionRecords: null,
  }
}

export function buildDamDigitalTwin(
  dam: DamFeature,
): DamDigitalTwin {
  return {
    identity: buildIdentity(dam),
    structural: buildStructural(dam),
    reservoir: buildReservoir(dam),
    hydrology: buildHydrology(),
    terrain: buildTerrain(),
    environment: buildEnvironment(),
    exposure: buildExposure(),
    monitoring: buildMonitoring(),
  }
}