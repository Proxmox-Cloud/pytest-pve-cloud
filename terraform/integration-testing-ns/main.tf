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
resource "kubernetes_config_map" "vcluster_config" {
  metadata {
    name      = "vcluster-config"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  data = {
    "vcluster.yaml" = <<-YAML
      policies:
        networkPolicy:
          enabled: true
        podSecurityStandard: baseline
    YAML
  }
}

resource "kubernetes_deployment" "api_deployment" {
  metadata {
    name      = "api-deployment"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    replicas = 1

    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = {
        app = "integration-api"
      }
    }

    template {
      metadata {
        labels = {
          app = "integration-api"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.integration_sa.metadata[0].name

        container {
          name  = "api"
          image = "harbor.tobias-huebner.tech/pxc-integration/pytest-pve-cloud:latest"

          port {
            container_port = 8080
          }

          command = ["integration-api"]

          volume_mount {
            name      = "vcluster-config"
            mount_path = "/app/vcluster.yaml"
            sub_path   = "vcluster.yaml"
          }
        }

        volume {
          name = "vcluster-config"
          config_map {
            name = kubernetes_config_map.vcluster_config.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "api_service" {
  metadata {
    name      = "integration-api"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    selector = {
      app = "integration-api"
    }

    port {
      port        = 80
      target_port = 8080
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}

# MCP Server deployment
resource "kubernetes_deployment" "vcluster_mcp_deployment" {
  metadata {
    name      = "vcluster-mcp-deployment"
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

          command = ["vcluster-mcp"]

          port {
            container_port = 8000
          }

          volume_mount {
            name       = "vcluster-config"
            mount_path = "/app/vcluster.yaml"
            sub_path   = "vcluster.yaml"
          }
        }

        volume {
          name = "vcluster-config"
          config_map {
            name = kubernetes_config_map.vcluster_config.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "vcluster_mcp_service" {
  metadata {
    name      = "vcluster-mcp"
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

resource "kubernetes_deployment" "helm_mcp_deployment" {
  metadata {
    name      = "helm-mcp-deployment"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    replicas = 1

    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = {
        app = "helm-mcp"
      }
    }

    template {
      metadata {
        labels = {
          app = "helm-mcp"
        }
      }

      spec {
        container {
          name  = "helm-mcp"
          image = "ghcr.io/zekker6/mcp-helm:v1.4.2"

          port {
            container_port = 8012
          }

          args = ["-mode=http"]
        }
      }
    }
  }
}

resource "kubernetes_service" "helm_mcp_service" {
  metadata {
    name      = "mcp-helm" # naming in accordance with artifact
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    selector = {
      app = "helm-mcp"
    }

    port {
      port        = 80
      target_port = 8012
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}

# vectordb + rag
resource "helm_release" "qdrant" {
  repository = "https://qdrant.github.io/qdrant-helm"
  chart = "qdrant"
  version = "1.20.0"
  name = "qdrant"
  namespace = kubernetes_namespace.namespace.metadata[0].name
}

resource "kubernetes_deployment" "source_rag_mcp_deployment" {
  metadata {
    name      = "source-rag-mcp-deployment"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    replicas = 1

    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = {
        app = "source-rag-mcp"
      }
    }

    template {
      metadata {
        labels = {
          app = "source-rag-mcp"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.integration_sa.metadata[0].name

        container {
          name  = "source-rag-mcp"
          image = "harbor.tobias-huebner.tech/pxc-integration/pytest-pve-cloud:latest"

          command = ["source-rag-mcp"]

          port {
            container_port = 8000
          }

          env {
            name = "MODEL_SERVER_URL"
            value = var.model_server_url
          }

          env {
            name = "MODEL_AUTH_HEADER_VALUE"
            value = var.model_auth_header_value
          }

          env {
            name = "MODEL_NAME"
            value = var.model_name
          }

          env {
            name = "J2_QUERY_TEMPLATE_B64"
            value = base64encode(var.j2_query_template)
          }

        }

        volume {
          name = "vcluster-config"
          config_map {
            name = kubernetes_config_map.vcluster_config.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "source_rag_mcp_service" {
  metadata {
    name      = "source-rag-mcp"
    namespace = kubernetes_namespace.namespace.metadata[0].name
  }

  spec {
    selector = {
      app = "source-rag-mcp"
    }

    port {
      port        = 80
      target_port = 8000
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}
