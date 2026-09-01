import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamReservoirData } from '../../types/digitalTwin';
import { IconDatabase } from './Icons';

export const DamReservoir: React.FC<{ data: DamReservoirData }> = ({ data }) => {
  const currentLevelStatus = data.currentLevel && data.frl && data.currentLevel > data.frl * 0.9 ? 'warning' : 'normal';
  
  return (
    <DataSection title="Reservoir Metrics" icon={<IconDatabase />}>
      <DataField label="Gross Storage" value={data.grossStorage} unit="Mm³" />
      <DataField label="Live Storage" value={data.liveStorage} unit="Mm³" />
      <DataField label="Dead Storage" value={data.deadStorage} unit="Mm³" />
      <DataField label="Current Level" value={data.currentLevel} unit="m" status={currentLevelStatus} />
      <DataField label="FRL" value={data.frl} unit="m" source="Design" />
      <DataField label="MWL" value={data.mwl} unit="m" source="Design" />
      <DataField label="Inflow" value={data.inflow} unit="m³/s" />
      <DataField label="Outflow" value={data.outflow} unit="m³/s" />
    </DataSection>
  );
};