{{/*
Expand the name of the chart.
*/}}
{{- define "iag5.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "iag5.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "iag5.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "iag5.labels" -}}
helm.sh/chart: {{ include "iag5.chart" . }}
{{ include "iag5.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "iag5.selectorLabels" -}}
app.kubernetes.io/name: {{ include "iag5.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "iag5.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "iag5.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Build etcd service DNS name
Usage: {{ include "iag5.etcdServiceDNS" . }}
*/}}
{{- define "iag5.etcdServiceDNS" -}}
{{- $etcdValues := .Values.etcd -}}
{{- $serviceName := "" -}}
{{- $namespace := .Release.Namespace -}}

{{/* Handle different etcd naming patterns */}}
{{- if and $etcdValues $etcdValues.fullnameOverride -}}
  {{- $serviceName = $etcdValues.fullnameOverride -}}
{{- else if and $etcdValues $etcdValues.nameOverride -}}
  {{- $serviceName = printf "%s-%s" .Release.Name $etcdValues.nameOverride -}}
{{- else -}}
  {{- $serviceName = printf "%s-etcd" .Release.Name -}}
{{- end -}}

{{- printf "%s.%s.svc.cluster.local" $serviceName $namespace -}}
{{- end -}}

{{/*
Build etcd service URL with port
Usage: {{ include "iag5.etcdServiceURL" . }}
*/}}
{{- define "iag5.etcdServiceURL" -}}
{{- $etcdPort := 2379 -}}
{{- printf "%s:%d" (include "iag5.etcdServiceDNS" .) $etcdPort -}}
{{- end -}}