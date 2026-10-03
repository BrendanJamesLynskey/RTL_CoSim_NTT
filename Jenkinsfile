// Jenkins pipeline for RTL_CoSim_NTT.
//
// Stages: lint (Verilator -Wall, Icarus parse) -> model tests (pytest, JUnit, coverage)
// -> RTL tests on Verilator (cocotb, JUnit) -> RTL tests on Icarus -> cycle-count
// performance gate against ci/cycle_baseline.json -> results.md -> an optional nightly
// seed sweep with longer campaigns.
//
// Needs on the agent: Verilator 5.x (VERILATOR_ROOT, or on PATH), Icarus Verilog 12,
// Python 3.10+, a C++ compiler. Plugins: Pipeline, Git, JUnit, Coverage.

pipeline {
    agent any

    parameters {
        booleanParam(name: 'NIGHTLY', defaultValue: false,
                     description: 'Longer campaigns, more core builds and the seed sweep (the nightly job sets this)')
        string(name: 'SWEEP_SEEDS', defaultValue: '8', description: 'Seeds per stimulus kind in the sweep')
        string(name: 'VERILATOR_ROOT', defaultValue: "${env.HOME}/.local/opt/verilator-deb/root/usr/share/verilator",
               description: 'Verilator install (empty: use the one on PATH)')
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '30'))
        timeout(time: 90, unit: 'MINUTES')
    }

    environment {
        // params are null on a job's very first build, before Jenkins has read the parameters block
        VERILATOR_ROOT = "${params.VERILATOR_ROOT ?: env.HOME + '/.local/opt/verilator-deb/root/usr/share/verilator'}"
        PATH = "${params.VERILATOR_ROOT ?: env.HOME + '/.local/opt/verilator-deb/root/usr/share/verilator'}/bin:${env.PATH}"
        MAKEFLAGS = '-j2'
    }

    stages {
        stage('Setup') {
            steps {
                sh '''
                    python3 -m venv .venv
                    .venv/bin/pip install -q -e ".[rtl,test]"
                '''
            }
        }

        stage('Lint') {
            steps {
                sh 'verilator --lint-only -Wall rtl/*.sv --top-module ntt_core'
                sh 'verilator --lint-only -Wall -GP=4 rtl/*.sv --top-module ntt_core'
                sh 'iverilog -g2012 -o /dev/null rtl/*.sv'
            }
        }

        stage('Model tests') {
            steps {
                sh '.venv/bin/pytest -m "not rtl" --junitxml=pytest-model.xml --cov=ntt_cosim --cov-report=xml:coverage.xml'
                recordCoverage(tools: [[parser: 'COBERTURA', pattern: 'coverage.xml']], sourceCodeRetention: 'LAST_BUILD')
            }
        }

        stage('RTL tests (Verilator)') {
            steps {
                sh "SIM=verilator NIGHTLY=${params.NIGHTLY ? 1 : 0} .venv/bin/pytest -m rtl --junitxml=pytest-verilator.xml"
            }
        }

        stage('RTL tests (Icarus)') {
            steps {
                sh 'SIM=icarus .venv/bin/pytest -m rtl --junitxml=pytest-icarus.xml'
            }
        }

        stage('Cycle-count gate') {
            steps {
                sh '.venv/bin/python ci/cycle_gate.py'
            }
            post {
                always { archiveArtifacts artifacts: 'perf_report.md', allowEmptyArchive: true }
            }
        }

        stage('Results') {
            steps {
                sh '.venv/bin/python examples/results.py > /dev/null'
                archiveArtifacts artifacts: 'examples/results.md'
            }
        }

        stage('Nightly seed sweep') {
            when { expression { params.NIGHTLY } }
            steps {
                sh ".venv/bin/python ci/seed_sweep.py ${params.SWEEP_SEEDS} 4000"
                archiveArtifacts artifacts: 'sweep.csv'
            }
        }
    }

    post {
        always {
            junit testResults: 'pytest-*.xml', allowEmptyResults: true
        }
    }
}
