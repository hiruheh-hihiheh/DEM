import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamExposureData } from '../../types/digitalTwin';
import { IconUsers } from './Icons';

export const DamExposure: React.FC<{ data: DamExposureData }> = ({ data }) => {
  const popStatus = data.population && data.population > 10000 ? 'critical' : 'normal';
  const hospitalStatus = data.hospitals && data.hospitals > 0 ? 'warning' : 'normal';
  const schoolStatus = data.schools && data.schools > 0 ? 'warning' : 'normal';

  return (
    <DataSection title="Downstream Exposure" icon={<IconUsers />}>
      <DataField label="Population" value={data.population} unit="people" status={popStatus} />
      <DataField label="Buildings" value={data.buildings} />
      <DataField label="Roads" value={data.roads} unit="km" />
      <DataField label="Bridges" value={data.bridges} />
      <DataField label="Hospitals" value={data.hospitals} status={hospitalStatus} />
      <DataField label="Schools" value={data.schools} status={schoolStatus} />
      <DataField label="Critical Infra" value={data.criticalInfrastructure} />
    </DataSection>
  );
};