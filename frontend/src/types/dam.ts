export interface DamProperties {
  pic: string | null
  name: string | null
  sdso: string | null
  state: string | null
  district: string | null
  river: string | null
  incharge: string | null
  height: number | null
  completion_year: number | null
  basin: string | null
  max_water_level: number | null
  full_reservoir_level: number | null
  gross_storage_capacity: number | null
  dead_storage_capacity: number | null
  dam_length: number | null
  dam_type: string | null
  purpose: string | null
}

export interface DamFeature {
  type: 'Feature'
  id: string
  properties: DamProperties
  geometry: {
    type: 'Point'
    coordinates: [number, number]
  }
}

export interface DamGeoJSON {
  type: 'FeatureCollection'
  features: DamFeature[]
}