FROM condaforge/miniforge3:latest

RUN apt-get update && \
    apt-get install -y --no-install-recommends openssh-server && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /run/sshd

RUN conda install -y -c conda-forge -c bioconda \
    snakemake-minimal pandas matplotlib scikit-learn && \
    conda clean -afy

RUN useradd -m -s /bin/bash testuser && \
    mkdir -p /home/testuser/.ssh && \
    chmod 700 /home/testuser/.ssh && \
    echo 'export PATH="/opt/conda/bin:$PATH"' >> /home/testuser/.bashrc && \
    echo 'export PATH="/opt/conda/bin:$PATH"' >> /home/testuser/.profile

RUN sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    echo "PermitRootLogin no" >> /etc/ssh/sshd_config && \
    echo "PermitUserEnvironment yes" >> /etc/ssh/sshd_config

# Ensure conda is on PATH for all sessions including SSH non-interactive
# /etc/environment is read by pam_env
RUN echo 'PATH="/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' > /etc/environment

COPY Snakefile config.yaml .lr-results .lr-agent-prompt /home/testuser/pipeline/
COPY scripts/ /home/testuser/pipeline/scripts/

RUN chown -R testuser:testuser /home/testuser

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D"]
