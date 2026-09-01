import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamEnvironmentData } from '../../types/digitalTwin';
import { IconLeaf } from './Icons';

export const DamEnvironment: React.FC<{ data: DamEnvironmentData }> = ({ data }) => (
  <DataSection title="Environment" icon={<IconLeaf />}>
    <DataField label="Air Quality (AQI)" value={data.airQuality} />
    <DataField label="Water Quality" value={data.waterQuality} />
    <DataField label="Temperature" value={data.temperature} unit="°C" />
    <DataField label="Weather" value={data.weather} />
    <DataField label="Soil & Geology" value={data.soilGeology} isText />
  </DataSection>
);