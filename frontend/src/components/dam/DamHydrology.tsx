import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamHydrologyData } from '../../types/digitalTwin';
import { IconWater } from './Icons';

export const DamHydrology: React.FC<{ data: DamHydrologyData }> = ({ data }) => (
  <DataSection title="Hydrology" icon={<IconWater />}>
    <DataField label="Rainfall" value={data.rainfall} unit="mm" />
    <DataField label="River Discharge" value={data.riverDischarge} unit="m³/s" />
    <DataField label="Upstream Inflow" value={data.upstreamInflow} unit="m³/s" />
    <DataField label="Forecast Rainfall" value={data.forecastRainfall} unit="mm" source="IMD" />
    <DataField label="Historical Extremes" value={data.historicalExtremes} isText />
  </DataSection>
);