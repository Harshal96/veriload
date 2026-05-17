{{- define "veriload-operator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "veriload-operator.namespace" -}}
{{- default .Release.Namespace .Values.namespaceOverride -}}
{{- end -}}

{{- define "veriload-operator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "veriload-operator.name" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "veriload-operator.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}

{{- define "veriload.k8sOperator.manifest" -}}
VeriLoad Kubernetes Operator chart for VeriLoadRun CRD, RBAC, and Deployment.
{{- end -}}
