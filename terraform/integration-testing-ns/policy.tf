# calico policy that restricts outgoing traffic

resource "kubernetes_manifest" "itgr_egress" {
  manifest = yamldecode(<<-YAML
    apiVersion: crd.projectcalico.org/v1
    kind: NetworkPolicy
    metadata:
      name: restrict-itgr-egress
      namespace: ${kubernetes_namespace.namespace.metadata[0].name}
    spec:
      selector: all()
      types:
        - Egress
      egress:
        # allow dns udp
        - action: Allow
          destination:
            ports:
              - 53
          protocol: UDP
        # allow dns tcp + https
        - action: Allow
          destination:
            ports:
              - 53
              - 443
          protocol: TCP
        # allow traffic to all other pods in namespace
        - action: Allow
          destination:
            selector: all()
        # allow access to the kubernetes api
        - action: Allow
          destination:
            services:
              name: kubernetes
              namespace: default
        # deny access to all private ip ranges
        - action: Deny
          destination:
            nets:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
        # allow access to public internet
        - action: Allow
          destination:
            nets:
              - 0.0.0.0/0

  YAML
  )
}
