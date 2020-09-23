import json
from kubernetes import client, config
from kubernetes.config import ConfigException
from kubernetes.client.rest import ApiException
from . import auth
from . import utils

SERVICE_ROLE_BINDING = "./kubeflow_jupyter/common/yaml/servicerolebinding-ml-pipeline.yaml"
ENVOY_FILTER = "./kubeflow_jupyter/common/yaml/envoyfilter-add-user-header.yaml"
logger = utils.create_logger(__name__)

try:
    # Load configuration inside the Pod
    config.load_incluster_config()
except ConfigException:
    # Load configuration for testing
    config.load_kube_config()

# Create the Apis
v1_core = client.CoreV1Api()
custom_api = client.CustomObjectsApi()
storage_api = client.StorageV1Api()


def parse_error(e):
    try:
        err = json.loads(e.body)["message"]
    except (json.JSONDecodeError, KeyError, AttributeError):
        err = str(e)

    return err


# Wrapper Functions for error handling
def wrap_resp(rsrc, fn, *args, **kwargs):
    '''
    rsrc: Name of the resource, used as the dict key
    fn: function to get the resource
    '''
    data = {
        "success": True,
        "log": ""
    }

    try:
        data[rsrc] = fn(*args, **kwargs)
    except ApiException as e:
        data[rsrc] = {}
        data["success"] = False
        data["log"] = parse_error(e)
    except Exception as e:
        data[rsrc] = {}
        data["success"] = False
        data["log"] = parse_error(e)

    return data


def wrap(fn, *args, **kwargs):
    '''
    fn: function to get the resource
    '''
    data = {
        "success": True,
        "log": ""
    }

    try:
        fn(*args, **kwargs)
    except ApiException as e:
        data["success"] = False
        data["log"] = parse_error(e)
    except Exception as e:
        data["success"] = False
        data["log"] = parse_error(e)

    return data


# API Functions
# GETers
@auth.needs_authorization("list", "", "v1", "persistentvolumeclaims")
def list_pvcs(namespace):
    return wrap_resp(
        "pvcs",
        v1_core.list_namespaced_persistent_volume_claim,
        namespace=namespace
    )


@auth.needs_authorization("list", "kubeflow.org", "v1beta1", "notebooks")
def list_notebooks(namespace):
    return wrap_resp(
        "notebooks",
        custom_api.list_namespaced_custom_object,
        "kubeflow.org",
        "v1beta1",
        namespace,
        "notebooks"
    )


# We don't do a subject access review on notebook events because
# notebook events are cluster scoped resources. Users however are only
# granted access to particular namespacs. We rely on the notebook webserver
# to filter out information a user shouldn't see.
def list_notebook_events(namespace, nb_name):
    '''
    V1EventList with events whose source the Notebook with 'nb_name' from namespace 'namespace'
    '''
    return wrap_resp(
        "notebook-events",
        v1_core.list_namespaced_event,
        namespace=namespace,
        field_selector="involvedObject.kind=Notebook,involvedObject.name=" + nb_name
    )


@auth.needs_authorization("list", "kubeflow.org", "v1alpha1", "poddefaults")
def list_poddefaults(namespace):
    return wrap_resp(
        "poddefaults",
        custom_api.list_namespaced_custom_object,
        "kubeflow.org",
        "v1alpha1",
        namespace,
        "poddefaults"
    )


@auth.needs_authorization("get", "", "v1", "secrets")
def get_secret(name, namespace):
    return wrap_resp(
        "secret",
        v1_core.read_namespaced_secret,
        name,
        namespace
    )


@auth.needs_authorization("list", "", "v1", "namespaces")
def list_namespaces():
    return wrap_resp(
        "namespaces",
        v1_core.list_namespace
    )

@auth.needs_authorization("get", "", "v1", "namespaces")
def read_namespace(namespace):
    return wrap_resp(
        "namespace",
        v1_core.read_namespace,
        namespace)

# @auth.needs_authorization("list", "storage.k8s.io", "v1", "storageclasses")
# NOTE: This function is only used from the backend in order to determine if a
# default StorageClass is set. Currently, the role aggregation does not use a
# ClusterRoleBinding, thus we can't currently give this permission to a user.
# The backend does not expose any endpoint that would allow an unauthorized
# user to list the storage classes using this function.
def list_storageclasses():
    return wrap_resp(
        "storageclasses",
        storage_api.list_storage_class
    )


# POSTers
@auth.needs_authorization("create", "kubeflow.org", "v1beta1", "notebooks")
def create_notebook(notebook, namespace):
    return wrap(
        custom_api.create_namespaced_custom_object,
        "kubeflow.org",
        "v1beta1",
        namespace,
        "notebooks",
        notebook
    )


@auth.needs_authorization("create", "", "v1", "persistentvolumeclaims")
def create_pvc(pvc, namespace):
    return wrap_resp(
        "pvc",
        v1_core.create_namespaced_persistent_volume_claim,
        namespace,
        pvc
    )

# DELETErs
@auth.needs_authorization("delete", "kubeflow.org", "v1beta1", "notebooks")
def delete_notebook(notebook_name, namespace):
    return wrap(
        custom_api.delete_namespaced_custom_object,
        "kubeflow.org",
        "v1beta1",
        namespace,
        "notebooks",
        notebook_name,
        client.V1DeleteOptions(propagation_policy="Foreground")
    )

def get_userid(namespace):
    # Get userid from metadata.annotations["owner"] of the namespace
    if namespace is None:
        return None
    data = read_namespace(namespace)
    logger.info(data["log"])
    if data["success"]:
        return data["namespace"].metadata.annotations.get("owner")

    return None

@auth.needs_authorization("create", "rbac.istio.io", "v1alpha1", "servicerolebindings")
def create_servicerolebinding(notebook_name, kf_namespace, namespace):
    # Create servicerolebinding for notebook server to access ml-pipeline
    servicerolebinding = utils.load_param_yaml(SERVICE_ROLE_BINDING,
                                                name=notebook_name,
                                                kf_namespace=kf_namespace,
                                                namespace=namespace)
    return wrap(
        custom_api.create_namespaced_custom_object,
        "rbac.istio.io",
        "v1alpha1",
        kf_namespace,
        "servicerolebindings",
        servicerolebinding
    )

@auth.needs_authorization("delete", "rbac.istio.io", "v1alpha1", "servicerolebindings")
def delete_servicerolebinding(notebook_name, kf_namespace, namespace):
    return wrap(
        custom_api.delete_namespaced_custom_object,
        "rbac.istio.io",
        "v1alpha1",
        kf_namespace,
        "servicerolebindings",
        "bind-ml-pipeline-nb-{namespace}-{name}".format(
            namespace=namespace,
            name=notebook_name
        ),
        client.V1DeleteOptions(propagation_policy="Foreground")
    )

@auth.needs_authorization("create", "networking.istio.io", "v1alpha3", "envoyfilters")
def create_envoy_filter(notebook_name, kf_namespace, namespace):
    # Create envoyfilter to inject user_header for outgoing traffics
    # from notebook server to ml-pipeline
    user_id = get_userid(namespace)
    if user_id is None:
        return

    envoyfilter = utils.load_param_yaml(ENVOY_FILTER,
                                        name=notebook_name,
                                        namespace=namespace,
                                        kf_namespace=kf_namespace,
                                        user_header=utils.get_user_header(),
                                        user_prefix=utils.get_user_prefix(),
                                        user_id=user_id)

    return wrap(
        custom_api.create_namespaced_custom_object,
        "networking.istio.io",
        "v1alpha3",
        namespace,
        "envoyfilters",
        envoyfilter
    )

@auth.needs_authorization("delete", "networking.istio.io", "v1alpha3", "envoyfilters")
def delete_envoy_filter(notebook_name, namespace):
    return wrap(
        custom_api.delete_namespaced_custom_object,
        "networking.istio.io",
        "v1alpha3",
        namespace,
        "envoyfilters",
        "add-header-{name}".format(name=notebook_name),
        client.V1DeleteOptions(propagation_policy="Foreground")
    )

# Readiness Probe helper
def can_connect_to_k8s():
    try:
        custom_api.list_namespaced_custom_object(
            "kubeflow.org",
            "v1beta1",
            "default",
            "notebooks",
        )
        return True

    except ApiException:
        return False

    return True
