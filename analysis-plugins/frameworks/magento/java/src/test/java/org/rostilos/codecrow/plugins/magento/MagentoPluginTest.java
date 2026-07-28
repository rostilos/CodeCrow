package org.rostilos.codecrow.plugins.magento;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.FileDisposition;
import org.rostilos.codecrow.plugins.PluginKind;

import static org.assertj.core.api.Assertions.assertThat;

class MagentoPluginTest {

    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new MagentoPlugin().descriptor();

        assertThat(descriptor.id()).isEqualTo("magento");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("php");
        assertThat(descriptor.detection().alternatives()).hasSize(5);
    }

    @Test
    void contributes_magento_file_policy_without_host_path_checks() {
        var plugin = new MagentoPlugin();

        assertThat(plugin.fileDisposition("generated/code/Acme/Proxy.php").value())
                .isEqualTo(FileDisposition.GENERATED);
        assertThat(plugin.fileDisposition("dev/tests/integration/Foo.php").value())
                .isEqualTo(FileDisposition.EXCLUDED);
        assertThat(plugin.fileDisposition("app/code/Acme/Checkout/etc/di.xml").value())
                .isEqualTo(FileDisposition.ARCHITECTURE_ONLY);
        assertThat(plugin.fileDisposition("app/code/Acme/Checkout/Model/Cart.php").value())
                .isEqualTo(FileDisposition.FULL);
        assertThat(plugin.fileDisposition("vendor/acme/module/Test/Unit/Foo.php").value())
                .isEqualTo(FileDisposition.EXCLUDED);
    }
}
