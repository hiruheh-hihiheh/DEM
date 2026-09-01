import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamTerrainData } from '../../types/digitalTwin';
import { IconMountain } from './Icons';

export const DamTerrain: React.FC<{ data: DamTerrainData }> = ({ data }) => (
  <DataSection title="Terrain & Geography" icon={<IconMountain />}>
    <DataField label="DEM Source" value={data.dem} />
    <DataField label="Slope" value={data.slope} unit="%" />
    <DataField label="Elevation" value={data.elevation} unit="m" />
    <DataField label="River Network" value={data.riverNetwork} isText />
    <DataField label="Land Cover" value={data.landCover} isText />
    <DataField label="Downstream Dist." value={data.downstreamDistance} unit="km" />
  </DataSection>
);