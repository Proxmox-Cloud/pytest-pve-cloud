# todo: annotate the namespace to force pod security level to restricted
resource "kubernetes_namespace" "namespace" {
  metadata {
    name = "pve-cloud-integration"
  }
}

resource "kubernetes_service_account" "integration_sa" {
  metadata {
    name      = "integration-sa"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }
}

resource "kubernetes_cluster_role_binding" "integration_sa_admin" {
  metadata {
    name = "integration-sa-admin-binding"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "cluster-admin"
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.integration_sa.metadata[0].name
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }
}

# vcluster creation config with maximum security constraints
# pod security context, network policy, ...

resource "kubernetes_deployment" "app_deployment" {
  metadata {
    name      = "app-deployment"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    replicas = 1

    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = {
        app = "vcluster-mcp"
      }
    }

    template {
      metadata {
        labels = {
          app = "vcluster-mcp"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.integration_sa.metadata[0].name

        container {
          name  = "vcluster-mcp"
          image = "harbor.tobias-huebner.tech/pxc-integration/pytest-pve-cloud:latest"

          port {
            container_port = 8000
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "app_service" {
  metadata {
    name      = "app-service"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    selector = {
      app = "vcluster-mcp"
    }

    port {
      port        = 80
      target_port = 8000
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}
