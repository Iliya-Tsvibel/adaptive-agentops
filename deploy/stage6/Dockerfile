FROM python:3.12.16-bookworm@sha256:4209fae09a3e20afd863e528bfddc235da5fd5977983e1996cd54cc9a72e6267
COPY .github/ci-requirements.txt /requirements.txt
RUN python -m pip --disable-pip-version-check install --no-compile --require-hashes --only-binary=:all: --target /opt/aao/deps -r /requirements.txt
COPY adaptive_agentops /opt/aao/adaptive_agentops
COPY scripts /opt/aao/scripts
RUN mkdir -m 0755 /lab && \
    printf '%s\n' 'agent:x:60101:60101::/nonexistent:/usr/sbin/nologin' 'controller:x:60102:60102::/nonexistent:/usr/sbin/nologin' 'target:x:60103:60103::/nonexistent:/usr/sbin/nologin' 'requester:x:60104:60104::/nonexistent:/usr/sbin/nologin' 'approver:x:60105:60105::/nonexistent:/usr/sbin/nologin' 'inspector:x:60106:60106::/nonexistent:/usr/sbin/nologin' 'wrong-peer:x:60109:60109::/nonexistent:/usr/sbin/nologin' >> /etc/passwd && \
    printf '%s\n' 'proposal:x:60111:agent,controller,wrong-peer' 'dispatch:x:60112:controller,target' 'operators:x:60113:controller,requester,approver' 'inspection:x:60116:controller,inspector' >> /etc/group
USER 60101:60101
