import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = (ROOT / "setup.sh").read_text(encoding="utf-8")


class SetupPersistenceTests(unittest.TestCase):
    def test_node_24_is_installed_with_nvm(self):
        self.assertIn('NODE_MAJOR="${NODE_MAJOR:-24}"', SETUP)
        self.assertLess(SETUP.index("install_nvm_node"), SETUP.index("install_prime_agent"))

    def test_all_login_and_interactive_shell_files_receive_tool_path(self):
        for filename in (".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile"):
            self.assertIn(filename, SETUP)
        self.assertIn('export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"', SETUP)

    def test_commands_are_published_to_usr_local_bin(self):
        self.assertIn("publish_tool_commands", SETUP)
        self.assertIn("node npm npx corepack prime-agent codegraph uv aws", SETUP)
        self.assertIn('destination="/usr/local/bin/$command_name"', SETUP)

    def test_prime_agent_skills_are_installed_after_prime_agent(self):
        self.assertIn("install_prime_agent_skills", SETUP)
        self.assertIn("mattpocock/skills", SETUP)
        self.assertIn("aws/agent-toolkit-for-aws", SETUP)
        self.assertIn("--global --agent pi --yes --copy --full-depth", SETUP)
        self.assertIn("aws-agent-toolkit-setup", SETUP)
        calls = SETUP.rsplit("install_prime_agent\n", 1)[1]
        self.assertIn("install_prime_agent_skills", calls)

    def test_clean_zsh_login_is_verified(self):
        self.assertIn("verify_login_shell_tools", SETUP)
        self.assertIn('zsh -lic "command -v $command_name"', SETUP)

    def test_existing_zshrc_gets_oh_my_zsh_bootstrap(self):
        self.assertIn("Overlord: oh-my-zsh", SETUP)
        self.assertIn('source $ZSH/oh-my-zsh.sh', SETUP)


if __name__ == "__main__":
    unittest.main()
