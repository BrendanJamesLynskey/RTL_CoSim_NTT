// Jenkins pipeline for RTL_CoSim_NTT.
//
// Stages: clean old reports -> lint (Verilator -Wall, Icarus parse) -> model tests (pytest, JUnit, coverage)
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
        string(name: 'VERILATOR_ROOT', defaultValue: '',
               description: 'Verilator install (empty: ~/.local/opt/verilator-deb/..., else the one on PATH)')
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '30'))
        timeout(time: 90, unit: 'MINUTES')
    }

    environment {
        // params are null on a job's very first build, before Jenkins has read the parameters block
        VR = "${params.VERILATOR_ROOT ?: ''}"
        MAKEFLAGS = '-j2'
        // Jenkins does not pass a PATH set here to sh steps, and HOME is only known to the shell,
        // so each step that needs Verilator sets it up itself
        VPATH = 'export VERILATOR_ROOT="${VR:-$HOME/.local/opt/verilator-deb/root/usr/share/verilator}"; [ -d "$VERILATOR_ROOT" ] && export PATH="$VERILATOR_ROOT/bin:$PATH" || unset VERILATOR_ROOT; '
    }

    stages {
        // The workspace is reused between builds (it keeps the virtualenv and build caches), so
        // delete the previous build's reports first. Without this a build that fails before its
        // tests run publishes the last build's JUnit results as its own (Rust_DES_Kernel #4 did).
        stage('Clean reports') {
            steps {
                sh 'rm -f pytest-*.xml coverage.xml perf_report.md sweep.csv'
            }
        }

        stage('Setup') {
            steps {
                sh '''
                    python3 -m venv .venv
                    .venv/bin/pip install -q -e ".[rtl,test]"
                    # pip keeps an installed git dependency whose version number has not changed, so
                    # fetch FHE_Accelerator_Sim's current commit every time.
                    .venv/bin/pip install -q --force-reinstall --no-deps "fhe-sim @ git+https://github.com/BrendanJamesLynskey/FHE_Accelerator_Sim"
                '''
            }
        }

        stage('Lint') {
            steps {
                sh(env.VPATH + 'verilator --lint-only -Wall rtl/*.sv --top-module ntt_core')
                sh(env.VPATH + 'verilator --lint-only -Wall -GP=4 rtl/*.sv --top-module ntt_core')
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
                sh(env.VPATH + "SIM=verilator NIGHTLY=${params.NIGHTLY ? 1 : 0} .venv/bin/pytest -m rtl --junitxml=pytest-verilator.xml")
            }
        }

        stage('RTL tests (Icarus)') {
            steps {
                sh(env.VPATH + 'SIM=icarus .venv/bin/pytest -m rtl --junitxml=pytest-icarus.xml')
            }
        }

        stage('Cycle-count gate') {
            steps {
                sh(env.VPATH + '.venv/bin/python ci/cycle_gate.py')
            }
            post {
                always { archiveArtifacts artifacts: 'perf_report.md', allowEmptyArchive: true }
            }
        }

        stage('Results') {
            steps {
                sh(env.VPATH + '.venv/bin/python examples/results.py > /dev/null')
                archiveArtifacts artifacts: 'examples/results.md'
            }
        }

        stage('Nightly seed sweep') {
            when { expression { params.NIGHTLY } }
            steps {
                sh(env.VPATH + ".venv/bin/python ci/seed_sweep.py ${params.SWEEP_SEEDS} 4000")
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
