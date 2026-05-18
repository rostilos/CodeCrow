package sample;

public class App {
    void run() {
        log.debug("start");
        riskyCall();
        leakedSecret();
    }
}
