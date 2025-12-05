package org.rostilos.codecrow.core.model.user;

import jakarta.persistence.*;
import java.time.Instant;

@Entity
@Table(name = "password_reset_tokens")
public class PasswordResetToken {
    
    private static final int EXPIRATION_HOURS = 1;
    
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    
    @Column(nullable = false, unique = true)
    private String token;
    
    @ManyToOne(fetch = FetchType.EAGER)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;
    
    @Column(name = "expiry_date", nullable = false)
    private Instant expiryDate;
    
    @Column(name = "used", nullable = false)
    private boolean used = false;
    
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    
    public PasswordResetToken() {
        this.createdAt = Instant.now();
    }
    
    public PasswordResetToken(String token, User user) {
        this.token = token;
        this.user = user;
        this.createdAt = Instant.now();
        this.expiryDate = Instant.now().plusSeconds(EXPIRATION_HOURS * 3600);
    }
    
    public boolean isExpired() {
        return Instant.now().isAfter(expiryDate);
    }
    
    public boolean isValid() {
        return !used && !isExpired();
    }

    public Long getId() {
        return id;
    }
    
    public void setId(Long id) {
        this.id = id;
    }
    
    public String getToken() {
        return token;
    }
    
    public void setToken(String token) {
        this.token = token;
    }
    
    public User getUser() {
        return user;
    }
    
    public void setUser(User user) {
        this.user = user;
    }
    
    public Instant getExpiryDate() {
        return expiryDate;
    }
    
    public void setExpiryDate(Instant expiryDate) {
        this.expiryDate = expiryDate;
    }
    
    public boolean isUsed() {
        return used;
    }
    
    public void setUsed(boolean used) {
        this.used = used;
    }
    
    public Instant getCreatedAt() {
        return createdAt;
    }
    
    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }
}
