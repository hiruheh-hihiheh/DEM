import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamIdentity } from '../../types/digitalTwin';
import { IconMap } from './Icons';

interface Props {
  data: DamIdentity;
}

export const DamOverview: React.FC<Props> = ({ data }) => {
  return (
    <DataSection title="Identity & Overview" icon={<IconMap />}>
      <DataField label="Dam Name" value={data.name} />
      <DataField label="Dam ID" value={data.damId} />
      <DataField label="River" value={data.river} />
      <DataField label="Basin" value={data.basin} />
      <DataField label="State" value={data.state} />
      <DataField label="District" value={data.district} />
      <DataField label="Latitude" value={data.latitude} unit="°" />
      <DataField label="Longitude" value={data.longitude} unit="°" />
    </DataSection>
  );
};