"""
Custom apps: the form's compose file, and the checks of a compose model
(no Docker needed).

    python3 -m unittest discover -s tests
"""

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.environ["CHARDSOFT_APPS_STATE"] = tempfile.mkdtemp(prefix="chardsoft-apps-test-")

loader = importlib.machinery.SourceFileLoader("apps_cli", os.path.join(ROOT, "bin", "chardsoft-apps"))
spec = importlib.util.spec_from_loader("apps_cli", loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


def model(**service):
    return {"services": {"web": {"image": "nginx", **service}}}


class FormTests(unittest.TestCase):

    def test_form_becomes_one_service(self):
        text, route = cli.form_compose({
            "image": "nginx:alpine", "port": "8080",
            "volumes": ["/config", "/config/", "/var/lib/x"], "env": {"A": "x $B"}
        })
        svc = json.loads(text)["services"]["app"]
        self.assertEqual(route, {"service": "app", "port": 8080})
        self.assertEqual(svc["image"], "nginx:alpine")
        self.assertEqual([v["source"] for v in svc["volumes"]],
                         ["${CHARDSOFT_DATA}/config", "${CHARDSOFT_DATA}/config-2", "${CHARDSOFT_DATA}/var-lib-x"])
        self.assertEqual(svc["environment"]["A"], "x $$B")
        self.assertEqual(svc["environment"]["TZ"], "${TZ}")

    def test_form_without_web_port(self):
        _, route = cli.form_compose({"image": "eclipse-mosquitto"})
        self.assertIsNone(route)

    def test_form_rejects_bad_input(self):
        for bad in ({"image": "a b"}, {"image": "x", "port": "99999"},
                    {"image": "x", "volumes": ["relative"]}, {"image": "x", "volumes": ["/"]},
                    {"image": "x", "env": {"1A": "x"}}, {"image": "x", "env": {"A": "two\nlines"}}):
            with self.assertRaises(cli.Fail, msg=bad):
                cli.form_compose(bad)


class CheckTests(unittest.TestCase):

    def check(self, m, route=None, app_id="demo"):
        return cli.check_model(app_id, m, route)

    def test_plain_app_is_fine(self):
        folder = str(cli.app_dir("demo"))
        m = model(volumes=[{"type": "bind", "source": folder + "/data/x", "target": "/x"}],
                  ports=[{"target": 81, "published": "9000", "host_ip": "127.0.0.1"}])
        self.assertEqual(self.check(m, {"service": "", "port": 80}), ([], []))

    def test_only_service_becomes_the_route(self):
        route = {"service": "", "port": 80}
        self.check(model(), route)
        self.assertEqual(route["service"], "web")

    def test_several_services_need_a_choice(self):
        m = {"services": {"a": {"image": "x"}, "b": {"image": "y"}}}
        errors, _ = self.check(m, {"service": "", "port": 80})
        self.assertIn("several services", errors[0])

    def test_build_and_router_ports_are_refused(self):
        errors, _ = self.check({"services": {"web": {"build": {"context": "."}}}})
        self.assertIn("builds an image", errors[0])
        for published in ("53", "80", "440-450"):
            errors, _ = self.check(model(ports=[{"target": 1, "published": published, "host_ip": "127.0.0.1"}]))
            self.assertTrue(errors, published)

    def test_risky_settings_are_listed(self):
        m = model(privileged=True, cap_add=["NET_ADMIN"], pid="host",
                  devices=[{"source": "/dev/ttyUSB0", "target": "/dev/ttyUSB0"}],
                  volumes=[{"type": "bind", "source": "/etc", "target": "/e", "read_only": True},
                           {"type": "bind", "source": "/var/run/docker.sock", "target": "/s"},
                           {"type": "volume", "source": "named", "target": "/v"}],
                  ports=[{"target": 81, "published": "8081"}, {"target": 1883}])
        errors, warnings = self.check(m)
        self.assertEqual(errors, [])
        text = " ".join(warnings)
        for word in ("privileged", "NET_ADMIN", "pid: host", "/dev/ttyUSB0", "/etc (read only)",
                     "docker.sock", "8081/tcp", "Docker picks"):
            self.assertIn(word, text)
        self.assertEqual(len(warnings), 8)

    def test_random_port_on_the_router_only_is_fine(self):
        errors, warnings = self.check(model(ports=[{"target": 8080, "host_ip": "127.0.0.1"}]))
        self.assertEqual((errors, warnings), ([], []))

    def test_path_outside_app_folder_is_not_inside(self):
        folder = str(cli.app_dir("demo"))
        _, warnings = self.check(model(volumes=[{"type": "bind", "source": folder + "-other", "target": "/x"}]))
        self.assertEqual(len(warnings), 1)

    def test_host_network(self):
        errors, warnings = self.check(model(network_mode="host"), {"service": "web", "port": 80})
        self.assertIn("router's own", errors[0])
        self.assertIn("host network", warnings[0])
        errors, _ = self.check(model(network_mode="host"), {"service": "web", "port": 8123})
        self.assertEqual(errors, [])


class RouteTests(unittest.TestCase):

    def test_route_is_published_on_localhost(self):
        m = cli.with_route(model(), {"service": "web", "port": 80}, 9200)
        self.assertEqual(m["services"]["web"]["ports"],
                         [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 80, "published": "9200", "protocol": "tcp"}])

    def test_host_network_needs_no_port(self):
        m = cli.with_route(model(network_mode="host"), {"service": "web", "port": 8123}, 8123)
        self.assertNotIn("ports", m["services"]["web"])

    def test_port_numbers(self):
        self.assertEqual(cli.port_numbers("80"), [80])
        self.assertEqual(cli.port_numbers("8000-8002"), [8000, 8001, 8002])
        self.assertEqual(cli.port_numbers("x"), [])


if __name__ == "__main__":
    unittest.main()
