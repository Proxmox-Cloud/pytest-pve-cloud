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

# todo: vcluster creation config with maximum security contstraints
# pod security context, network policy, ...
resource "kubernetes_config_map" "app_config" {
  metadata {
    name      = "app-config"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  data = {
    "entrypoint.sh" = file("${path.module}/entrypoint.sh")
    "api.py"     = file("${path.module}/api.py")
    "vcluster.yaml" = <<-YAML
      policies:
        networkPolicy:
          enabled: true
        podSecurityStandard: baseline
    YAML
  }
}

resource "kubernetes_persistent_volume_claim" "venv_pvc" {
  metadata {
    name      = "venv-pvc"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = {
        storage = "1Gi"
      }
    }
  }
}

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
        app = "integration-app"
      }
    }

    template {
      metadata {
        labels = {
          app = "integration-app"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.integration_sa.metadata[0].name

        container {
          name  = "app"
          image = "python:3.13-trixie"

          command = ["/bin/bash", "/app/entrypoint.sh"]

          port {
            container_port = 8080
          }

          volume_mount {
            name       = "app-volume"
            mount_path = "/app"
            read_only  = true
          }

          volume_mount {
            name       = "venv-volume"
            mount_path = "/app-venv"
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }
        }

        volume {
          name = "app-volume"
          config_map {
            name = kubernetes_config_map.app_config.metadata[0].name
          }
        }

        volume {
          name = "venv-volume"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.venv_pvc.metadata[0].name
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
      app = "integration-app"
    }

    port {
      port        = 80
      target_port = 8080
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}
