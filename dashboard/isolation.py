import io
import os

import docker

import config


def _build_iptables_image(client) -> None:
    """Build the lightweight iptables utility image if not already present."""
    try:
        client.images.get(config._IPTABLES_IMAGE)
    except docker.errors.ImageNotFound:
        dockerfile = io.BytesIO(b"FROM alpine:3.21\nRUN apk add --no-cache iptables\n")
        client.images.build(fileobj=dockerfile, tag=config._IPTABLES_IMAGE, rm=True)


def _apply_network_isolation(client, network_name: str, bot_name: str) -> bool:
    """Apply iptables rules blocking LAN access for a bot's network. Returns True on success."""
    try:
        network = client.networks.get(network_name)
    except docker.errors.NotFound:
        return False

    network_id = network.id[:12]
    chain = f"CF-{bot_name[:25]}"
    llm_host = os.environ.get("LLM_HOST", "")
    llm_port = os.environ.get("LLM_PORT", "")

    llm_rule = ""
    if llm_host and llm_port:
        llm_rule = f"iptables -A {chain} -d {llm_host} -p tcp --dport {llm_port} -j RETURN"

    script = f"""
        iptables -N {chain} 2>/dev/null || iptables -F {chain}
        iptables -A {chain} -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
        {llm_rule}
        iptables -A {chain} -d 10.0.0.0/8 -j DROP
        iptables -A {chain} -d 172.16.0.0/12 -j DROP
        iptables -A {chain} -d 192.168.0.0/16 -j DROP
        iptables -A {chain} -j RETURN
        iptables -C DOCKER-USER -i br-{network_id} -j {chain} 2>/dev/null || \
        iptables -I DOCKER-USER -i br-{network_id} -j {chain}
    """

    try:
        client.containers.run(
            config._IPTABLES_IMAGE,
            command=["sh", "-c", script],
            network_mode="host",
            cap_add=["NET_ADMIN"],
            remove=True,
        )
        return True
    except Exception as e:
        config.log.warning("Network isolation failed for %s: %s", bot_name, e)
        return False


def _remove_network_isolation(client, network_name: str, bot_name: str) -> bool:
    """Remove iptables rules for a bot's network. Returns True on success."""
    network_id = ""
    try:
        network = client.networks.get(network_name)
        network_id = network.id[:12]
    except docker.errors.NotFound:
        pass

    chain = f"CF-{bot_name[:25]}"

    # If we don't have a network_id, we can't remove the DOCKER-USER jump rule,
    # but we can still flush and delete the chain itself.
    delete_jump = (
        f'iptables -D DOCKER-USER -i br-{network_id} -j {chain} 2>/dev/null || true'
        if network_id else "true"
    )

    script = f"""
        {delete_jump}
        iptables -F {chain} 2>/dev/null || true
        iptables -X {chain} 2>/dev/null || true
    """

    try:
        client.containers.run(
            config._IPTABLES_IMAGE,
            command=["sh", "-c", script],
            network_mode="host",
            cap_add=["NET_ADMIN"],
            remove=True,
        )
        return True
    except Exception:
        return False
