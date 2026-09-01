import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamStructuralData } from '../../types/digitalTwin';
import { IconBuilding } from './Icons';

export const DamStructural: React.FC<{ data: DamStructuralData }> = ({ data }) => (
  <DataSection title="Structural Data" icon={<IconBuilding />}>
    <DataField label="Height" value={data.height} unit="m" />
    <DataField label="Length" value={data.length} unit="m" />
    <DataField label="Dam Type" value={data.damType} />
    <DataField label="Foundation" value={data.foundationType} />
    <DataField label="Crest Elevation" value={data.crestElevation} unit="m" />
    <DataField label="Spillway Capacity" value={data.spillwayCapacity} unit="m³/s" />
    <DataField label="Construction Year" value={data.constructionYear} unit="AD" />
    <DataField label="Gate Info" value={data.gateInformation} isText />
    <DataField label="Inspection Info" value={data.inspectionInformation} isText />
  </DataSection>
);